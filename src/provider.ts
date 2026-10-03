import http from 'node:http';
import https from 'node:https';
import { PassThrough } from 'node:stream';
import { createBrotliDecompress, createGunzip, createInflate } from 'node:zlib';
import type { Data, Json, Message, Model, StreamItem, ToolSpec, Usage } from './types.js';
import { Cancelled, ModelError, PersistenceError, errorText } from './types.js';
import type { Config } from './config.js';
import { EventBus } from './events.js';

export const sensitiveName = (name: string): boolean => /authorization|cookie|token|secret|key|password/i.test(name);
export function safeUrl(raw: string): string {
  const url = new URL(raw); url.username = url.password = '';
  for (const key of url.searchParams.keys()) if (sensitiveName(key)) url.searchParams.set(key, '[REDACTED]');
  return url.toString();
}
export const safeHeaders = (headers: string[]): Json[] => {
  const result: Json[] = [];
  for (let i = 0; i < headers.length; i += 2) result.push([headers[i], sensitiveName(headers[i]) ? '[REDACTED]' : headers[i + 1]]);
  return result;
};
export function encodeMessage(message: Message): Data {
  const data: Data = { role: message.role, content: message.content };
  if (message.tool_calls?.length) data.tool_calls = message.tool_calls.map((c) => ({ id: c.id, type: 'function', function: { name: c.name, arguments: c.arguments } }));
  if (message.tool_call_id) data.tool_call_id = message.tool_call_id;
  if (message.reasoning) data.reasoning_content = message.reasoning;
  return data;
}
type Chunk = { choices?: { index: number; delta?: { content?: string; reasoning_content?: string; reasoning?: string;
  tool_calls?: { index: number; id?: string; function?: { name?: string; arguments?: string } }[] }; finish_reason?: string | null }[]; usage?: Usage };
export class ChatModel implements Model {
  name: string; config: Config; bus: EventBus;
  constructor(config: Config, bus: EventBus) {
    if (!config.api_key) throw new ModelError('No API key configured. Set OPENAI_API_KEY in the environment or .env.');
    this.name = config.model; this.config = config; this.bus = bus;
  }
  async *stream(input: { system: string; messages: Message[]; tools: ToolSpec[]; signal: AbortSignal }): AsyncGenerator<StreamItem> {
    const request_id = this.bus.request_id!;
    const url = new URL((this.config.base_url || 'https://api.openai.com/v1').replace(/\/$/, '') + '/chat/completions');
    const body: Data = { model: this.name, messages: [{ role: 'system', content: input.system }, ...input.messages.map(encodeMessage)], stream: true };
    if (input.tools.length) body.tools = input.tools.map((t) => ({ type: 'function', function: { name: t.name, description: t.description, parameters: t.parameters } }));
    if (this.config.stream_usage) body.stream_options = { include_usage: true };
    const bytes = Buffer.from(JSON.stringify(body));
    const headers = { Authorization: `Bearer ${this.config.api_key}`, 'Content-Type': 'application/json',
      'Content-Length': String(bytes.length), 'Accept-Encoding': 'gzip, deflate, br', Host: url.host, Connection: 'close' };
    this.bus.publish('api_request', { request_id, method: 'POST', url: safeUrl(url.toString()), headers: safeHeaders(Object.entries(headers).flat()), body_base64: bytes.toString('base64') });
    let response: http.IncomingMessage | undefined, ended = false, rawError: unknown, storageFailure = false;
    let request: http.ClientRequest | undefined;
    const finish = (status: string, extra: Data = {}): void => { if (!ended) { ended = true; this.bus.publish('api_response_end', { request_id, status, ...extra }); } };
    const abort = (): void => { request?.destroy(new Cancelled()); response?.destroy(new Cancelled()); };
    const timer = setTimeout(() => request?.destroy(new ModelError('model request timed out')), this.config.request_timeout * 1000);
    input.signal.addEventListener('abort', abort, { once: true });
    try {
      if (input.signal.aborted) throw new Cancelled();
      response = await new Promise<http.IncomingMessage>((resolve, reject) => {
        request = (url.protocol === 'https:' ? https : http).request(url, { method: 'POST', headers }, resolve);
        request.on('error', reject); request.end(bytes);
      });
      this.bus.publish('api_response_start', { request_id, status_code: response.statusCode || 0, headers: safeHeaders(response.rawHeaders) });
      const encoding = response.headers['content-encoding'];
      const decoder = encoding === 'gzip' ? createGunzip() : encoding === 'deflate' ? createInflate() : encoding === 'br' ? createBrotliDecompress() : new PassThrough();
      response.on('data', (chunk: Buffer) => {
        try { this.bus.publish('api_response_chunk', { request_id, body_base64: chunk.toString('base64') }); }
        catch (error) { rawError = error; response?.destroy(error as Error); }
      });
      response.on('error', (error) => decoder.destroy(error)); response.pipe(decoder);
      const utf8 = new TextDecoder(); let buffer = '', text = '', reasoning = '', finishReason: string | null = null, usage: Usage | null = null, done = false;
      const calls = new Map<number, { id: string; name: string; arguments: string }>();
      let errorBody = '';
      for await (const data of decoder) {
        if (input.signal.aborted) throw new Cancelled();
        const fragment = utf8.decode(data as Buffer, { stream: true });
        if ((response.statusCode || 500) >= 400) { errorBody += fragment; continue; }
        buffer += fragment;
        let boundary: RegExpExecArray | null;
        while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
          const frame = buffer.slice(0, boundary.index); buffer = buffer.slice(boundary.index + boundary[0].length);
          const payload = frame.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
          if (!payload) continue;
          if (payload === '[DONE]') { done = true; continue; }
          if (done) throw new ModelError('SSE payload arrived after [DONE]');
          const original: unknown = JSON.parse(payload);
          if (!original || typeof original !== 'object' || Array.isArray(original)) throw new ModelError('invalid model chunk');
          this.bus.publish('api_response_parsed', { request_id, chunk: original as Json });
          const chunk = original as Chunk;
          if (chunk.usage) usage = { prompt_tokens: chunk.usage.prompt_tokens || 0, completion_tokens: chunk.usage.completion_tokens || 0, total_tokens: chunk.usage.total_tokens || 0 };
          const choice = chunk.choices?.find((c) => c.index === 0); if (!choice) continue;
          finishReason = choice.finish_reason || finishReason;
          if (choice.delta?.content) { text += choice.delta.content; yield { type: 'text', text: choice.delta.content }; }
          const thinking = choice.delta?.reasoning_content || choice.delta?.reasoning;
          if (thinking) { reasoning += thinking; yield { type: 'reasoning', text: thinking }; }
          for (const part of choice.delta?.tool_calls || []) {
            const call = calls.get(part.index) || { id: '', name: '', arguments: '' };
            if (part.id) call.id += part.id; if (part.function?.name) call.name += part.function.name;
            if (part.function?.arguments) call.arguments += part.function.arguments; calls.set(part.index, call);
          }
        }
      }
      if (rawError) throw rawError;
      if ((response.statusCode || 500) >= 400) throw new ModelError(`HTTP ${response.statusCode}: ${errorBody}`);
      if (!done || !finishReason) throw new ModelError('model stream ended without a complete response');
      const tool_calls = [...calls.entries()].sort(([a], [b]) => a - b).map(([, c]) => c);
      if (tool_calls.some((c) => !c.id || !c.name) || new Set(tool_calls.map((c) => c.id)).size !== tool_calls.length) throw new ModelError('invalid tool-call identity');
      finish('complete'); yield { type: 'response', text, reasoning, tool_calls, finish_reason: finishReason, usage };
    } catch (error) {
      if (rawError instanceof PersistenceError || error instanceof PersistenceError) { storageFailure = true; throw rawError instanceof PersistenceError ? rawError : error; }
      finish(input.signal.aborted || error instanceof Cancelled ? 'cancelled' : 'error', { message: errorText(error), error_type: error instanceof Error ? error.name : 'Error' });
      if (input.signal.aborted || error instanceof Cancelled) throw new Cancelled();
      throw new ModelError(errorText(error));
    } finally {
      clearTimeout(timer); input.signal.removeEventListener('abort', abort); response?.destroy(); request?.destroy();
      if (!ended && !storageFailure) finish(input.signal.aborted ? 'cancelled' : 'closed');
    }
  }
}
