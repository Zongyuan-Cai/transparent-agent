import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { parseArgs } from 'node:util';
import { configFromEnv } from '../dist/config.js';
import { SessionManager } from '../dist/web/worker.js';
import { WebServer } from '../dist/web/server.js';
import { mockApi, sse, textChunks } from './mock-api.ts';

const { values } = parseArgs({ options: { port: { type: 'string', default: '8766' } } });
const root = mkdtempSync(join(tmpdir(), 'transparent-demo-')), workspace = join(root, 'demo-project'); mkdirSync(workspace); mkdirSync(join(workspace, '.git'));
writeFileSync(join(workspace, 'AGENTS.md'), 'This is an offline demonstration. Work only inside this temporary workspace.');
const api = await mockApi((body, res) => {
  const messages = body.messages as { role: string; content: string; tool_call_id?: string }[], last = messages.at(-1)!;
  res.writeHead(200, { 'Content-Type': 'text/event-stream' });
  if (last.role === 'tool' && last.tool_call_id === 'check') {
    const chunks = textChunks('已在临时工作区创建 `demo-result.txt`，并用 shell 验证文件内容。\n\n你可以在右侧检查完整 API 请求、响应字节与工具输出。');
    let i = 0; const timer = setInterval(() => { if (i < chunks.length) res.write(`data: ${JSON.stringify(chunks[i++])}\n\n`); else { clearInterval(timer); res.end('data: [DONE]\n\n'); } }, 250); res.on('close', () => clearInterval(timer)); return;
  }
  const call = last.role === 'tool' ? { id: 'check', name: 'bash', arguments: JSON.stringify({ command: 'cat demo-result.txt; printf "\\nverification passed\\n"' }) }
    : { id: 'write', name: 'write', arguments: JSON.stringify({ path: 'demo-result.txt', content: 'Transparent Agent · TypeScript\n离线演示文件\n' }) };
  res.end(sse([{ choices: [{ index: 0, delta: { reasoning_content: '先检查工具调用的完整记录。', tool_calls: [{ index: 0, id: call.id, function: { name: call.name, arguments: call.arguments } }] }, finish_reason: 'tool_calls' }] }]));
});
const config = configFromEnv({ workspace, model: 'offline-demo', api_key: 'demo-only', base_url: api.url });
const server = await new WebServer(new SessionManager(config, { state_dir: join(root, 'state'), home: join(root, 'home') }), '127.0.0.1', Number(values.port)).listen();
process.stdout.write(`Offline TypeScript demo: ${server.url}\nTemporary workspace: ${workspace}\nNo real model API is used.\n`);
try { await new Promise<void>((resolve) => { const stop = () => { process.off('SIGINT', stop); process.off('SIGTERM', stop); resolve(); }; process.on('SIGINT', stop); process.on('SIGTERM', stop); }); }
finally { await server.close(); await api.close(); rmSync(root, { recursive: true, force: true }); }
