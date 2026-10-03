import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { appendFileSync, readFileSync, writeFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { SessionManager } from '../dist/web/worker.js';
import { WebServer } from '../dist/web/server.js';
import { TraceView } from '../dist/web/trace-view.js';
import { Runtime } from '../dist/index.js';
import { mockApi, sse, textChunks } from '../scripts/mock-api.ts';
import { fixture, waitUntil, FakeModel } from './helpers.ts';

test('Web API runs in independent process; SSE replay, inspection, busy rejection, cancel and clear', async (t) => {
  const f = fixture(); t.after(f.clean); const api = await mockApi();
  const manager = new SessionManager({ ...f.config, base_url: api.url }, f.options), server = await new WebServer(manager, '127.0.0.1', 0).listen();
  t.after(async () => { await server.close(); await api.close(); });
  const get = async (path: string) => { const res = await fetch(server.url + path); assert.equal(res.status, 200); return res.json(); };
  const token = (await get('/api/config')).token, post = (path: string, body: object) => fetch(server.url + path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Transparent-Token': token }, body: JSON.stringify(body) });
  assert.match(await (await fetch(server.url)).text(), /Transparent/); const created = await post('/api/sessions', {}); assert.equal(created.status, 201); const id = (await created.json()).session_id, path = `/api/sessions/${id}`;
  assert.equal((await post(path + '/runs', { text: 'slow' })).status, 202); assert.equal((await post(path + '/runs', { text: 'second' })).status, 409);
  await waitUntil(() => api.requests.length === 1);
  const streamAbort = new AbortController(); const stream = await fetch(server.url + path + '/stream', { signal: streamAbort.signal }); const reader = stream.body!.getReader(); const first = new TextDecoder().decode((await reader.read()).value); assert.match(first, /retry: 1000/); await reader.cancel(); streamAbort.abort();
  assert.equal((await get(path)).status, 'running'); // Browser disconnect does not stop the worker.
  assert.equal((await post(path + '/cancel', {})).status, 202); await waitUntil(() => manager.status(id).status === 'idle'); assert.equal(manager.status(id).result?.stop_reason, 'cancelled');
  assert.equal((await post(path + '/runs', { text: 'finish' })).status, 202); await waitUntil(() => manager.status(id).status === 'idle');
  const detail = await get(path); assert.equal(detail.messages.at(-1).content, 'verified 中文');
  const page = await get(path + '/events?limit=500'); const seq = page.events.find((e: { type: string }) => e.type === 'api_request').seq;
  const original = await get(path + `/events/${seq}`), request = await get(path + `/requests/${original.data.request_id}`); assert.match(Buffer.from(original.data.body_base64, 'base64').toString(), /slow/); assert.ok(request.records.length > 2);
  const finalRequestId = page.events.filter((e: { type: string }) => e.type === 'api_request').at(-1).data.request_id;
  const full = await get(path + `/requests/${finalRequestId}`); assert.match(full.response_text, /extra_unknown/); assert.ok(full.response_body_base64);
  const reconnect = await fetch(server.url + path + `/stream?after=0`, { headers: { 'Last-Event-ID': String(page.total - 1) } }); const replay = reconnect.body!.getReader(); const bytes = new TextDecoder().decode((await replay.read()).value); await replay.cancel(); assert.match(bytes, new RegExp(`id: ${page.total}\\n`)); assert.doesNotMatch(bytes, /id: 1\n/);
  const download = await fetch(server.url + path + '/trace'); const content = await download.text(); assert.equal(Buffer.byteLength(content), Number(download.headers.get('content-length'))); assert.equal(content.trim().split('\n').length, page.total);
  assert.equal((await post(path + '/clear', {})).status, 200); assert.deepEqual((await get(path)).messages, []); assert.ok(manager.trace(id).page().total > page.total);
});
test('Web boundaries reject cross-origin writes, bad token, IDs, body size and non-loopback', async (t) => {
  const f = fixture(); t.after(f.clean); const manager = new SessionManager(f.config, f.options); assert.throws(() => new WebServer(manager, '0.0.0.0'), /loopback/);
  const server = await new WebServer(manager, '127.0.0.1', 0).listen(); t.after(() => server.close()); const config = await (await fetch(server.url + '/api/config')).json();
  assert.equal((await fetch(server.url + '/api/config', { headers: { Origin: 'https://evil.example' } })).status, 403);
  const badHost = await new Promise<number | undefined>((resolve, reject) => { const req = http.get(server.url + '/api/config', { headers: { Host: 'evil.example' } }, (res) => { res.resume(); resolve(res.statusCode); }); req.on('error', reject); });
  assert.equal(badHost, 403);
  assert.equal((await fetch(server.url + '/api/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status, 403);
  const headers = { 'Content-Type': 'application/json', 'X-Transparent-Token': config.token };
  assert.equal((await fetch(server.url + '/api/sessions', { method: 'POST', headers, body: JSON.stringify({ model: ' ' }) })).status, 400);
  assert.equal((await fetch(server.url + '/api/sessions', { method: 'POST', headers, body: JSON.stringify({ text: 'a'.repeat(1024 * 1024) }) })).status, 413);
  assert.equal((await fetch(server.url + '/api/sessions/invalid')).status, 404);
  assert.equal((await fetch(server.url + '/../../package.json')).status, 404);
});
test('workers execute real tools, enforce shared session locks and evict idle sessions', async (t) => {
  const f = fixture(); t.after(f.clean);
  const api = await mockApi((body, res) => {
    const messages = body.messages as { role: string }[]; res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    if (messages.at(-1)!.role === 'tool') res.end(sse(textChunks('file written')));
    else res.end(sse([{ choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: 'write', function: { name: 'write', arguments: '{"path":"web-result.txt","content":"verified"}' } }] }, finish_reason: 'tool_calls' }] }]));
  }); t.after(api.close); const manager = new SessionManager({ ...f.config, base_url: api.url }, f.options, 1); t.after(() => manager.close());
  const first = await manager.create(); await manager.send(first.session_id, 'write'); await waitUntil(() => manager.status(first.session_id).status === 'idle'); assert.equal(readFileSync(join(f.workspace, 'web-result.txt'), 'utf8'), 'verified');
  await assert.rejects(Runtime.create(f.config, { ...f.options, resume: manager.path(first.session_id) }, undefined, new FakeModel()), /locked/);
  const second = await manager.create(); assert.equal(manager.workers.has(first.session_id), false); assert.ok(manager.workers.has(second.session_id));
  await manager.send(first.session_id, 'resume'); await waitUntil(() => manager.status(first.session_id).status === 'idle'); assert.equal(manager.status(first.session_id).result?.text, 'file written');
  assert.equal(manager.workers.has(second.session_id), false);
});
test('incremental trace index waits for newline, keeps full records, detects truncation', (t) => {
  const f = fixture(); t.after(f.clean); const path = join(f.root, 'index.jsonl'), id = 'a'.repeat(32);
  const record = { schema_version: 1, session_id: id, run_id: null, seq: 1, type: 'tool_call_end', timestamp: new Date().toISOString(), data: { output: '中文'.repeat(10000), full_output: 'full' } };
  const bytes = Buffer.from(JSON.stringify(record) + '\n'); writeFileSync(path, bytes.subarray(0, 100)); const view = new TraceView(path, id); assert.equal(view.page().total, 0);
  appendFileSync(path, bytes.subarray(100)); assert.equal(view.page().total, 1); assert.equal(view.page().events[0].data.output!.toString().length, 8192); assert.equal(view.record(1).data.output, record.data.output);
  assert.equal(statSync(path).size, bytes.length); writeFileSync(path, ''); assert.throws(() => view.refresh(), /truncated/);
});
