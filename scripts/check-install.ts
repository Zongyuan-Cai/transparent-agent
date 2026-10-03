import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { spawn, spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';
import { mockApi } from './mock-api.ts';

if (!process.argv[2]) throw new Error('Usage: node scripts/check-install.ts PACKAGE.tgz');
const tarball = resolve(process.argv[2]), root = mkdtempSync(join(tmpdir(), 'transparent-install-')), workspace = join(root, 'workspace'); mkdirSync(workspace);
const run = (file: string, args: string[]) => {
  const result = spawnSync(file, args, { cwd: root, encoding: 'utf8', env: process.env });
  if (result.error || result.status !== 0) throw new Error(`${file} failed: ${result.error || result.stderr || result.stdout}`); return result.stdout;
};
let web: ReturnType<typeof spawn> | undefined, api: Awaited<ReturnType<typeof mockApi>> | undefined;
try {
  assert.doesNotMatch(run('tar', ['-tzf', tarball]), /\.(py|pyc|whl)(?:\n|$)/);
  writeFileSync(join(root, 'package.json'), JSON.stringify({ name: 'installation-check', private: true, type: 'module', allowScripts: { 'fs-ext@2.1.1': true } }));
  run('npm', ['install', tarball, '--no-audit', '--no-fund']);
  const bin = join(root, 'node_modules/.bin'); assert.match(run(join(bin, 'transparent-agent'), ['--help']), /Transparent Agent/);
  assert.match(run(join(bin, 'transparent-agent-web'), ['--help']), /transparent-agent-web/);
  api = await mockApi(); let output = '';
  web = spawn(join(bin, 'transparent-agent-web'), ['-C', workspace, '--port', '0', '--state-dir', join(root, 'state')], { cwd: root, env: { ...process.env, OPENAI_API_KEY: 'test', OPENAI_BASE_URL: api.url }, stdio: ['ignore', 'pipe', 'pipe'] });
  web.stdout!.on('data', (b) => { output += b; }); web.stderr!.on('data', (b) => { output += b; });
  const until = Date.now() + 10000; while (!output.match(/http:\/\/127\.0\.0\.1:\d+/)) { if (Date.now() > until || web.exitCode !== null) throw new Error(output || 'Web startup failed'); await new Promise((r) => setTimeout(r, 50)); }
  const url = output.match(/http:\/\/127\.0\.0\.1:\d+/)![0];
  for (const path of ['/', '/app.js', '/styles.css', '/api/config']) assert.equal((await fetch(url + path)).status, 200);
  const config = await (await fetch(url + '/api/config')).json(), headers = { 'Content-Type': 'application/json', 'X-Transparent-Token': config.token };
  const session = await (await fetch(url + '/api/sessions', { method: 'POST', headers, body: '{}' })).json(); assert.ok(session.session_id);
  const path = `/api/sessions/${session.session_id}`; assert.equal((await fetch(url + path + '/runs', { method: 'POST', headers, body: '{"text":"verify installed package"}' })).status, 202);
  let detail; do { await new Promise((r) => setTimeout(r, 50)); detail = await (await fetch(url + path)).json(); if (Date.now() > until) throw new Error('installed agent timed out'); } while (detail.status === 'running');
  assert.equal(detail.result?.stop_reason, 'final'); assert.equal(detail.messages.at(-1).content, 'verified 中文');
  process.stdout.write('Installed tarball verified outside the repository: CLI, Web assets, worker, model and trace.\n');
} finally {
  if (web && web.exitCode === null) { const exited = new Promise((r) => web!.once('exit', r)); web.kill('SIGTERM'); await exited; }
  await api?.close(); rmSync(root, { recursive: true, force: true });
}
