import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, symlinkSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { buildRegistry, Cancelled, ToolInterrupted } from '../dist/index.js';
import { fixture } from './helpers.ts';

test('file ranges, CRLF edits, invalid text and workspace symlinks', async (t) => {
  const f = fixture(); t.after(f.clean); const registry = buildRegistry(), ctx = { workspace: f.workspace, bash_timeout: 1, max_bash_timeout: 2, max_output_chars: 30000, signal: new AbortController().signal };
  const run = (name: string, args: object) => registry.execute(name, JSON.stringify(args), ctx);
  assert.equal((await run('write', { path: 'dir/中文.txt', content: '甲\r\n乙\r\n丙\r\n' })).is_error, undefined);
  assert.match((await run('read', { path: 'dir/中文.txt', start_line: 2, end_line: 2 })).output, /2\t乙/);
  assert.equal((await run('edit', { path: 'dir/中文.txt', old_string: '甲\n乙', new_string: '丁\n戊' })).is_error, undefined);
  assert.equal(readFileSync(join(f.workspace, 'dir/中文.txt'), 'utf8'), '丁\r\n戊\r\n丙\r\n');
  await run('write', { path: 'duplicate', content: 'x x' }); assert.equal((await run('edit', { path: 'duplicate', old_string: 'x', new_string: 'y' })).is_error, true);
  await run('edit', { path: 'duplicate', old_string: 'x', new_string: 'y', replace_all: true }); assert.equal(readFileSync(join(f.workspace, 'duplicate'), 'utf8'), 'y y');
  writeFileSync(join(f.workspace, 'binary'), Buffer.from([0, 255])); assert.equal((await run('read', { path: 'binary' })).is_error, true);
  writeFileSync(join(f.workspace, 'invalid'), Buffer.from([255])); assert.equal((await run('read', { path: 'invalid' })).is_error, true);
  symlinkSync(f.root, join(f.workspace, 'outside'));
  for (const [tool, args] of Object.entries({ read: { path: 'outside/nope' }, write: { path: 'outside/nope', content: 'bad' }, edit: { path: 'outside/nope', old_string: 'a', new_string: 'b' }, ls: { path: 'outside' }, find: { path: 'outside', pattern: '*' }, grep: { path: 'outside', pattern: 'hello' } })) {
    const result = await run(tool, args); assert.equal(result.is_error, true); assert.match(result.output, /outside the workspace/);
  }
  assert.equal((await run('write', { path: '../escape', content: 'bad' })).is_error, true);
  symlinkSync(join(f.root, 'missing'), join(f.workspace, 'dangling')); assert.equal((await run('write', { path: 'dangling', content: 'bad' })).is_error, true);
});
test('find and grep limits, glob, binary skip, malformed and cancellable regex', async (t) => {
  const f = fixture(); t.after(f.clean); const registry = buildRegistry(), ctx = { workspace: f.workspace, bash_timeout: 1, max_bash_timeout: 2, max_output_chars: 30000, signal: new AbortController().signal };
  mkdirSync(join(f.workspace, 'src')); writeFileSync(join(f.workspace, 'src/main.ts'), 'HELLO 中文\nhello\n'); writeFileSync(join(f.workspace, 'data.bin'), Buffer.from([0, 104, 101, 108, 108, 111]));
  assert.match((await registry.execute('find', '{"pattern":"*.ts"}', ctx)).output, /src\/main.ts/);
  assert.match((await registry.execute('grep', '{"pattern":"hello","ignore_case":true,"glob":"*.ts"}', ctx)).output, /2 match/);
  assert.equal((await registry.execute('grep', '{"pattern":"["}', ctx)).is_error, true);
  writeFileSync(join(f.workspace, 'slow'), 'a'.repeat(100000) + '!'); const abort = new AbortController(); setTimeout(() => abort.abort(), 100);
  await assert.rejects(registry.execute('grep', '{"pattern":"(a+)+$","path":"slow"}', { ...ctx, signal: abort.signal }), Cancelled);
});
test('shell streams, exit, timeout and cancellation keep original bytes and kill group', async (t) => {
  const f = fixture(); t.after(f.clean); const registry = buildRegistry(), ctx = { workspace: f.workspace, bash_timeout: .15, max_bash_timeout: .2, max_output_chars: 500, signal: new AbortController().signal };
  const success = await registry.execute('bash', '{"command":"printf hello; printf error >&2"}', ctx); assert.equal(success.metadata?.stdout, 'hello'); assert.equal(success.metadata?.stderr, 'error'); assert.equal(success.metadata?.stdout_base64, Buffer.from('hello').toString('base64'));
  const failure = await registry.execute('bash', '{"command":"exit 3"}', ctx); assert.equal(failure.metadata?.exit_code, 3); assert.equal(failure.is_error, true);
  const timeout = await registry.execute('bash', '{"command":"echo partial; sleep 60","timeout_s":5}', ctx); assert.equal(timeout.metadata?.timeout, true); assert.match(timeout.output, /clamped/);
  const abort = new AbortController(); setTimeout(() => abort.abort(), 50); await assert.rejects(registry.execute('bash', '{"command":"printf \\\\377; sleep 60"}', { ...ctx, signal: abort.signal }), ToolInterrupted);
  const start = performance.now(); await registry.execute('bash', '{"command":"sleep 60 & echo $! > background.pid; exit 0"}', ctx); assert.ok(performance.now() - start < 2500);
  const pid = readFileSync(join(f.workspace, 'background.pid'), 'utf8').trim(); let state = ''; try { state = readFileSync(`/proc/${pid}/stat`, 'utf8').split(' ')[2]; } catch { /* Process already reaped. */ } assert.ok(!state || state === 'Z', `descendant still running: ${state}`);
});
