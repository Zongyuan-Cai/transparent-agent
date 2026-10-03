import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { Runtime, readTrace } from '../dist/index.js';
import { TraceView } from '../dist/web/trace-view.js';
import { FakeModel, fixture, answer } from './helpers.ts';

test('real Python v1 snapshot and trace resume without replaying effects', async (t) => {
  const f = fixture(); t.after(f.clean); const trace = join(f.root, 'legacy-trace.jsonl'), session = join(f.root, 'legacy-session.json');
  const saved = JSON.parse(readFileSync(new URL('./fixtures/python-v1/session.json', import.meta.url), 'utf8'));
  saved.workspace = f.workspace; saved.trace_path = trace; writeFileSync(session, JSON.stringify(saved));
  writeFileSync(trace, readFileSync(new URL('./fixtures/python-v1/trace.jsonl', import.meta.url)));
  const model = new FakeModel([answer('migrated')]); model.name = 'legacy-model';
  const runtime = await Runtime.create(f.config, { ...f.options, resume: session }, undefined, model); t.after(() => runtime.close());
  assert.equal(runtime.agent.system, 'LEGACY_SYSTEM preserved exactly'); assert.equal(runtime.agent.history.at(-1)!.tool_call_id, 'pending'); assert.equal(existsSync(join(f.workspace, 'marker')), false);
  assert.equal((await runtime.agent.send('continue')).text, 'migrated'); assert.equal(model.requests[0].messages[0].content, 'legacy request 中文');
  const records = [...readTrace(trace)]; assert.deepEqual(records.map((r) => r.seq), records.map((_, i) => i + 1));
  const view = new TraceView(trace, saved.session_id); assert.equal(view.page().total, records.length); assert.equal(view.record(2).data.text, 'legacy request 中文');
});
