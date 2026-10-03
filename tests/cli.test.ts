import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { mockApi } from '../scripts/mock-api.ts';
import { fixture, waitUntil } from './helpers.ts';

function cli(args: string[], env: NodeJS.ProcessEnv, lines?: string) {
  const child = spawn(process.execPath, [fileURLToPath(new URL('../bin/agent.mjs', import.meta.url)), ...args], { env: { ...process.env, ...env }, stdio: ['pipe', 'pipe', 'pipe'] });
  let stdout = '', stderr = ''; child.stdout.on('data', (b) => { stdout += b; }); child.stderr.on('data', (b) => { stderr += b; });
  if (lines !== undefined) child.stdin.end(lines);
  return { child, output: () => ({ stdout, stderr }), done: new Promise<{ code: number | null; stdout: string; stderr: string }>((resolve, reject) => { child.on('error', reject); child.on('exit', (code) => resolve({ code, stdout, stderr })); }) };
}
test('CLI print stdout, piped multiline and queued input, resume, inspect and SIGINT', async (t) => {
  const f = fixture(); t.after(f.clean); const api = await mockApi(); t.after(api.close); const env = { OPENAI_API_KEY: 'test', OPENAI_BASE_URL: api.url };
  const args = ['-C', f.workspace, '--state-dir', f.options.state_dir]; const printed = await cli([...args, '-p', 'hello'], env).done;
  assert.equal(printed.code, 0); assert.equal(printed.stdout, 'verified 中文\n'); assert.match(printed.stderr, /trace:/); const path = printed.stderr.match(/session: (.+)\n/)![1];
  const resumed = await cli(['--resume', path, '-p', 'continue'], env).done; assert.equal(resumed.code, 0); const saved = JSON.parse(readFileSync(path, 'utf8')); assert.equal(saved.messages.length, 4);
  const inspected = await cli(['--inspect', saved.trace_path], { ...env, OPENAI_API_KEY: '' }).done; assert.equal(inspected.code, 0); assert.match(inspected.stdout, /api_response_chunk/);
  const before = api.requests.length; const piped = await cli(args, env, 'first\\\nsecond\nthird\n/status\n/exit\n').done; assert.equal(piped.code, 0); assert.equal(api.requests.length, before + 2);
  const messages = api.requests[before].body.messages as { content: string }[]; assert.equal(messages.at(-1)!.content, 'first\nsecond');
  const slow = cli([...args, '-p', 'slow'], env); t.after(() => slow.child.kill('SIGKILL')); await waitUntil(() => slow.output().stdout.includes('waiting')); slow.child.kill('SIGINT'); assert.equal((await slow.done).code, 130);
});
