import { test } from 'node:test';
import assert from 'node:assert/strict';
import { gunzipSync } from 'node:zlib';
import { ChatModel, EventBus, Cancelled, ModelError, PersistenceError, safeUrl } from '../dist/index.js';
import type { Event, StreamItem } from '../src/types.js';
import { fixture } from './helpers.ts';
import { mockApi, sse, textChunks } from '../scripts/mock-api.ts';

test('wire request equals server bytes; compressed original and unknown fields remain visible', async (t) => {
  const f = fixture(); t.after(f.clean); const api = await mockApi(); t.after(api.close); const events: Event[] = [], bus = new EventBus('a'.repeat(32), { emit: (e) => events.push(e) }); bus.request_id = 'request';
  const model = new ChatModel({ ...f.config, base_url: api.url }, bus), items: StreamItem[] = [];
  for await (const item of model.stream({ system: 'system', messages: [{ role: 'user', content: 'hello 中文' }], tools: [], signal: new AbortController().signal })) items.push(item);
  assert.equal(items.at(-1)!.type, 'response'); assert.equal(items.at(-1)!.text, 'verified 中文');
  const request = events.find((e) => e.type === 'api_request')!; assert.deepEqual(Buffer.from(String(request.data.body_base64), 'base64'), api.requests[0].bytes);
  assert.equal(api.requests[0].headers.authorization, 'Bearer test-secret'); assert.doesNotMatch(JSON.stringify(events), /test-secret|sensitive/);
  const wire = Buffer.concat(events.filter((e) => e.type === 'api_response_chunk').map((e) => Buffer.from(String(e.data.body_base64), 'base64'))); assert.match(gunzipSync(wire).toString(), /extra_unknown/);
  assert.ok(events.some((e) => e.type === 'api_response_parsed' && (e.data.chunk as Record<string, unknown>).extra_unknown));
  assert.equal(events.filter((e) => e.type === 'api_response_end').length, 1); assert.equal(events.at(-1)!.data.status, 'complete');
  assert.doesNotMatch(safeUrl('https://u:p@example.org/v1?api_key=secret&x=1'), /secret|u:p/);
});
test('UTF-8 split chunks and fragmented tool call arguments reconstruct exactly', async (t) => {
  const f = fixture(); t.after(f.clean);
  const chunks = [
    { choices: [{ index: 0, delta: { content: '中文🙂', tool_calls: [{ index: 0, id: 'call-', function: { name: 'wr', arguments: '{"path":' } }] } }] },
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: '1', function: { name: 'ite', arguments: '"中文","content":"ok"}' } }] }, finish_reason: 'tool_calls' }] },
  ];
  const api = await mockApi((_b, res) => { res.writeHead(200, { 'Content-Type': 'text/event-stream' }); const bytes = Buffer.from(sse(chunks)); for (let i = 0; i < bytes.length; i++) res.write(bytes.subarray(i, i + 1)); res.end(); }); t.after(api.close);
  const model = new ChatModel({ ...f.config, base_url: api.url }, new EventBus('a'.repeat(32), { emit() {} })); const items: StreamItem[] = [];
  for await (const item of model.stream({ system: '', messages: [], tools: [], signal: new AbortController().signal })) items.push(item);
  const response = items.at(-1)!; assert.equal(response.text, '中文🙂'); assert.ok(response.type === 'response'); assert.deepEqual(response.tool_calls, [{ id: 'call-1', name: 'write', arguments: '{"path":"中文","content":"ok"}' }]);
});
test('HTTP failures, incomplete stream, timeouts and cancellation terminate cleanly', async (t) => {
  const f = fixture(); t.after(f.clean);
  for (const mode of ['http', 'partial', 'timeout', 'cancel']) {
    const api = await mockApi((_b, res) => {
      if (mode === 'http') { res.writeHead(429); res.end('quota error'); }
      else if (mode === 'partial') { res.writeHead(200); res.end(sse(textChunks(), false)); }
      else { res.writeHead(200); res.write(': waiting\n\n'); }
    });
    const model = new ChatModel({ ...f.config, base_url: api.url, request_timeout: .15 }, new EventBus('a'.repeat(32), { emit() {} })), abort = new AbortController();
    if (mode === 'cancel') setTimeout(() => abort.abort(), 30);
    await assert.rejects(async () => { for await (const _item of model.stream({ system: '', messages: [], tools: [], signal: abort.signal })) { /* consume */ } }, mode === 'cancel' ? Cancelled : ModelError);
    await api.close();
  }
});
test('raw recorder failure is fatal, never converted into normal model error', async (t) => {
  const f = fixture(); t.after(f.clean); const api = await mockApi(); t.after(api.close);
  const model = new ChatModel({ ...f.config, base_url: api.url }, new EventBus('a'.repeat(32), { emit(e) { if (e.type === 'api_response_chunk') throw new PersistenceError('disk full'); } }));
  await assert.rejects(async () => { for await (const _item of model.stream({ system: '', messages: [], tools: [], signal: new AbortController().signal })) { /* consume */ } }, PersistenceError);
});
