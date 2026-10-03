import http from 'node:http';
import type { ServerResponse } from 'node:http';
import { gzipSync } from 'node:zlib';

export function sse(chunks: object[], done = true) { return chunks.map((chunk) => `data: ${JSON.stringify(chunk)}\n\n`).join('') + (done ? 'data: [DONE]\n\n' : ''); }
export function textChunks(text = 'verified 中文', finish = 'stop') { return [
  { id: 'mock', choices: [{ index: 0, delta: { reasoning_content: 'Checking the workspace. ' }, finish_reason: null }], extra_unknown: { keep: true } },
  { choices: [{ index: 0, delta: { content: text }, finish_reason: null }] },
  { choices: [{ index: 0, delta: {}, finish_reason: finish }], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } },
]; }
export async function mockApi(handler?: (body: Record<string, unknown>, res: ServerResponse, bytes: Buffer) => void) {
  const requests: { body: Record<string, unknown>; bytes: Buffer; headers: http.IncomingHttpHeaders }[] = [];
  const server = http.createServer(async (req, res) => {
    const chunks: Buffer[] = []; for await (const chunk of req) chunks.push(chunk as Buffer);
    const bytes = Buffer.concat(chunks); let body: Record<string, unknown>;
    try { body = JSON.parse(bytes.toString('utf8')); } catch { res.writeHead(400); res.end('invalid JSON'); return; }
    requests.push({ body, bytes, headers: req.headers });
    if (handler) { handler(body, res, bytes); return; }
    const messages = body.messages as { role: string; content: string; tool_call_id?: string }[];
    const user = messages.findLast((m) => m.role === 'user')?.content || '';
    if (user.includes('slow')) {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' }); res.write('data: {"choices":[{"index":0,"delta":{"content":"waiting"}}]}\n\n');
      const timer = setInterval(() => res.write(': keepalive\n\n'), 100); res.on('close', () => clearInterval(timer)); return;
    }
    const payload = Buffer.from(sse(textChunks())); res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Content-Encoding': 'gzip', 'X-Mock': 'original', 'Set-Cookie': 'sensitive' }); res.end(gzipSync(payload));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = (server.address() as { port: number }).port;
  return { server, requests, url: `http://127.0.0.1:${port}/v1`, close: () => new Promise<void>((resolve) => { server.closeAllConnections(); server.close(() => resolve()); }) };
}
