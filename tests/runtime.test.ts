import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdirSync, existsSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { Runtime, readTrace, SessionStore, PersistenceError, EventBus, ToolRegistry, Resources, configFromEnv, parseDotenv, repairPending } from '../dist/index.js';
import { FakeModel, fixture, answer, calls } from './helpers.ts';

test('runtime tools, lazy skills, lossless truncation, and saved context resume', async (t) => {
  const f = fixture(); t.after(f.clean);
  writeFileSync(join(f.workspace, 'AGENTS.md'), 'PROJECT_RULE'); const dir = join(f.workspace, '.agents/skills/review'); mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'SKILL.md'), '---\nname: review\ndescription: Review code\n---\nLAZY_BODY');
  const model = new FakeModel([calls({ id: 's', name: 'load_skill', arguments: '{"name":"review"}' }), calls({ id: 'w', name: 'write', arguments: '{"path":"result.txt","content":"verified"}' }, { id: 'b', name: 'bash', arguments: '{"command":"seq 1 10000"}' }), answer()]);
  const runtime = await Runtime.create(f.config, f.options, undefined, model); t.after(() => runtime.close());
  assert.match(runtime.agent.system, /PROJECT_RULE/); assert.doesNotMatch(runtime.agent.system, /LAZY_BODY/);
  assert.equal((await runtime.agent.send('fix it')).stop_reason, 'final'); assert.match(model.requests[1].messages.at(-1)!.content!, /LAZY_BODY/);
  assert.equal(readFileSync(join(f.workspace, 'result.txt'), 'utf8'), 'verified');
  const records = [...readTrace(runtime.trace_path)], output = records.find((r) => r.type === 'tool_call_end' && r.data.name === 'bash')!.data;
  assert.equal(output.truncated, true); assert.match(String(output.full_output), /5000\n5001/); assert.ok(String(output.output).length <= 500);
  assert.equal(statSync(runtime.session_path).mode & 0o777, 0o600); assert.equal(statSync(runtime.trace_path).mode & 0o777, 0o600);
  assert.throws(() => new SessionStore(runtime.session_path), /locked/);
  assert.equal(spawnSync('flock', ['-n', runtime.session_path.replace(/\.json$/, '.lock'), 'true']).status, 1);
  const old = runtime.trace_path, path = runtime.session_path; await runtime.close(); writeFileSync(join(f.workspace, 'AGENTS.md'), 'NEW_RULE');
  const next = new FakeModel([answer('continued')]), resumed = await Runtime.create(f.config, { ...f.options, resume: path }, undefined, next); t.after(() => resumed.close());
  assert.match(resumed.agent.system, /PROJECT_RULE/); assert.doesNotMatch(resumed.agent.system, /NEW_RULE/); assert.equal((await resumed.agent.send('continue')).text, 'continued');
  assert.ok(next.requests[0].messages.length > 5); const all = [...readTrace(old)]; assert.deepEqual(all.map((r) => r.seq), all.map((_, i) => i + 1));
});
test('class tools retain their receiver and cancellation cannot swallow persistence failure', async (t) => {
  const f = fixture(); t.after(f.clean);
  class Example { name = 'example'; description = ''; parameters = { type: 'object', additionalProperties: false }; value = 'receiver'; run() { return { output: this.value }; } }
  const registry = new ToolRegistry([new Example()]), context = { workspace: f.workspace, bash_timeout: 1, max_bash_timeout: 2, max_output_chars: 500, signal: new AbortController().signal };
  assert.equal((await registry.execute('example', '{}', context)).output, 'receiver');
  const model = new FakeModel([calls({ id: 'bad', name: 'fail', arguments: '{}' })]), abort = new AbortController();
  const runtime = await Runtime.create(f.config, f.options, undefined, model); t.after(() => runtime.close());
  runtime.agent.tools.register({ name: 'fail', description: '', parameters: { type: 'object' }, run() { abort.abort(); throw new PersistenceError('required write failed'); } });
  await assert.rejects(runtime.agent.send('go', abort.signal), PersistenceError);
});
test('event serialization and snapshot write failure are fatal before model execution', async (t) => {
  const f = fixture(); t.after(f.clean); const bus = new EventBus('a'.repeat(32), { emit() { throw new Error('must not be reached'); } });
  assert.throws(() => bus.publish('bad', { value: NaN }), PersistenceError); const circular: { self?: unknown } = {}; circular.self = circular; assert.throws(() => bus.publish('bad', circular), PersistenceError);
  const model = new FakeModel([answer()]), runtime = await Runtime.create(f.config, f.options, undefined, model); t.after(() => runtime.close());
  const previous = readFileSync(runtime.session_path); runtime.store.save = () => { throw new PersistenceError('save failed'); };
  await assert.rejects(runtime.agent.send('go'), /save failed/); assert.equal(model.requests.length, 0); assert.deepEqual(readFileSync(runtime.session_path), previous);
});
test('pending calls repaired without executing effects; invalid ordering rejected', async (t) => {
  const f = fixture(); t.after(f.clean); const runtime = await Runtime.create(f.config, f.options, undefined, new FakeModel());
  runtime.agent.history = [{ role: 'user', content: 'write' }, { role: 'assistant', content: null, tool_calls: [{ id: 'pending', name: 'write', arguments: '{"path":"marker","content":"bad"}' }] }]; runtime.save(); const path = runtime.session_path; await runtime.close();
  const resumed = await Runtime.create(f.config, { ...f.options, resume: path }, undefined, new FakeModel()); t.after(() => resumed.close());
  assert.match(resumed.agent.history.at(-1)!.content!, /not replayed/); assert.equal(existsSync(join(f.workspace, 'marker')), false);
  assert.throws(() => repairPending([{ role: 'tool', content: 'bad', tool_call_id: 'orphan' }]), PersistenceError);
});
test('observer mutation isolated; mandatory trace failure prevents side effect', async (t) => {
  const f = fixture(); t.after(f.clean); const model = new FakeModel([calls({ id: 'w', name: 'write', arguments: '{"path":"marker","content":"bad"}' })]);
  const runtime = await Runtime.create(f.config, f.options, { emit(event) { if (event.type === 'model_request_start') event.data.messages = []; } }, model); t.after(() => runtime.close());
  const original = runtime.recorder.emit.bind(runtime.recorder); runtime.recorder.emit = (event) => { if (event.type === 'tool_call_start') throw new PersistenceError('disk full'); original(event); };
  await assert.rejects(runtime.agent.send('original'), /disk full/); assert.equal(model.requests[0].messages[0].content, 'original'); assert.equal(existsSync(join(f.workspace, 'marker')), false); assert.equal(runtime.agent.running, false);
});
test('cancellation fills each pending result and permits next turn', async (t) => {
  const f = fixture(); t.after(f.clean); const model = new FakeModel([calls({ id: 'b', name: 'bash', arguments: '{"command":"echo partial; sleep 60"}' }, { id: 'w', name: 'write', arguments: '{"path":"marker","content":"bad"}' }), answer('retry')]);
  const runtime = await Runtime.create(f.config, f.options, undefined, model); t.after(() => runtime.close()); const abort = new AbortController();
  setTimeout(() => abort.abort(), 200); assert.equal((await runtime.agent.send('go', abort.signal)).stop_reason, 'cancelled');
  assert.equal(existsSync(join(f.workspace, 'marker')), false); assert.deepEqual(runtime.agent.history.filter((m) => m.role === 'tool').map((m) => m.tool_call_id), ['b', 'w']);
  assert.equal((await runtime.agent.send('retry')).text, 'retry');
});
test('limits, malformed model response and schema errors are explicit', async (t) => {
  const f = fixture(); t.after(f.clean); const model = new FakeModel([calls({ id: 'c', name: 'read', arguments: '{"path":"missing","extra":true}' })]);
  const runtime = await Runtime.create({ ...f.config, max_steps: 1 }, f.options, undefined, model); t.after(() => runtime.close()); assert.equal((await runtime.agent.send('go')).stop_reason, 'max_steps');
  assert.match(runtime.agent.history.at(-1)!.content!, /invalid arguments/);
  const empty = await Runtime.create(f.config, f.options, undefined, new FakeModel([{ type: 'text', text: 'partial' }])); t.after(() => empty.close()); assert.equal((await empty.agent.send('go')).stop_reason, 'error');
});
test('resource priority, explicit skills, path boundary, templates and ESM extensions', async (t) => {
  const f = fixture(); t.after(f.clean); const root = join(f.workspace, '.agents/skills'), dir = join(root, 'manual'); mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'SKILL.md'), '---\nname: manual\ndescription: Manual only\ndisable-model-invocation: true\n---\nBODY'); const prompts = join(f.workspace, '.agents/prompts'); mkdirSync(prompts, { recursive: true }); writeFileSync(join(prompts, 'review.md'), 'Review $ARGUMENTS');
  const resources = new Resources(f.workspace, { home: f.options.home }); assert.doesNotMatch(resources.instructions(), /Manual only/); assert.throws(() => resources.loadSkill('manual'), /explicit/); assert.match(resources.expand('/skill:manual task'), /BODY/); assert.equal(resources.expand('/prompt:review $(echo unsafe)'), 'Review $(echo unsafe)'); assert.throws(() => resources.loadSkill('manual', '../else', true), /inside/);
  const extension = join(f.workspace, 'extension.mjs'); writeFileSync(extension, 'export function register(api) { api.registerTool({name:"hello",description:"example",parameters:{type:"object",additionalProperties:false},run:()=>({output:"hello"})}); }');
  const runtime = await Runtime.create(f.config, { ...f.options, extensions: [extension] }, undefined, new FakeModel()); t.after(() => runtime.close()); assert.ok(runtime.agent.tools.names().includes('hello'));
});
test('configuration precedence and validation; nested tool schemas', async () => {
  assert.deepEqual(parseDotenv('export A="a b"\nB=value # comment\n#ignored'), { A: 'a b', B: 'value' });
  assert.throws(() => configFromEnv({ max_steps: NaN }), /finite/); assert.throws(() => configFromEnv({ base_url: 'file:///tmp' }), /http/);
  const registry = new ToolRegistry([{ name: 'nested', description: '', parameters: { type: 'object', properties: { value: { type: 'array', items: { type: 'integer' } } }, required: ['value'], additionalProperties: false }, run: () => ({ output: 'valid' }) }]);
  const context = { workspace: process.cwd(), bash_timeout: 1, max_bash_timeout: 2, max_output_chars: 200, signal: new AbortController().signal };
  assert.equal((await registry.execute('nested', '{"value":[1,"oops"]}', context)).is_error, true); assert.equal((await registry.execute('nested', '{"value":[1,2]}', context)).output, 'valid');
});
