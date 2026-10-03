import { readdir, stat } from 'node:fs/promises';
import { join, relative } from 'node:path';
import { Worker } from 'node:worker_threads';
import { minimatch } from 'minimatch';
import type { Data, Tool, ToolContext, ToolResult } from '../types.js';
import { Cancelled, ToolError } from '../types.js';
import { schema } from './files.js';
import { label, workspacePath } from './base.js';

const skip = new Set(['.git', '.hg', '.svn', 'node_modules', '.venv', 'venv', '__pycache__', 'dist', 'build', '.pytest_cache']);
async function find(args: Data, ctx: ToolContext): Promise<ToolResult> {
  const root = workspacePath(ctx, args.path || '.'), pattern = String(args.pattern), matches: string[] = [];
  if (!(await stat(root)).isDirectory()) throw new ToolError('find path must be a directory');
  const deadline = Date.now() + 30000;
  async function visit(path: string): Promise<void> {
    for (const entry of (await readdir(path, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      if (ctx.signal.aborted) throw new Cancelled();
      if (matches.length >= 200 || Date.now() > deadline) return;
      if (skip.has(entry.name)) continue;
      const full = join(path, entry.name), directory = entry.isDirectory();
      const candidate = pattern.includes('/') ? relative(root, full) : entry.name;
      if ((!args.type || args.type === 'any' || (args.type === 'dir') === directory) && minimatch(candidate, pattern, { dot: true })) matches.push(label(ctx, full) + (directory ? '/' : ''));
      if (directory) await visit(full);
    }
  }
  await visit(root);
  return { output: `${matches.length} match(es) under ${label(ctx, root)}:\n${matches.sort().join('\n')}${matches.length >= 200 || Date.now() > deadline ? '\n[search limited; narrow scope]' : ''}` };
}
async function grep(args: Data, ctx: ToolContext): Promise<ToolResult> {
  const root = workspacePath(ctx, args.path || '.'); await stat(root);
  try { new RegExp(String(args.pattern), args.ignore_case ? 'i' : ''); } catch { throw new ToolError('invalid regular expression'); }
  if (ctx.signal.aborted) throw new Cancelled();
  const worker = new Worker(new URL('./search-worker.js', import.meta.url), { workerData: { root, workspace: ctx.workspace, ...args } });
  try {
    const result = await new Promise<{ matches: string[]; limited: boolean }>((resolve, reject) => {
      const stop = (): void => { reject(new Cancelled()); };
      const timer = setTimeout(() => reject(new ToolError('search exceeded 30 seconds; narrow pattern or scope')), 30000);
      ctx.signal.addEventListener('abort', stop, { once: true });
      const cleanup = (): void => { clearTimeout(timer); ctx.signal.removeEventListener('abort', stop); };
      worker.once('message', (data: { matches: string[]; limited: boolean }) => { cleanup(); resolve(data); });
      worker.once('error', (error) => { cleanup(); reject(error); });
      worker.once('exit', (code) => { cleanup(); if (code) reject(new ToolError(`search worker exited ${code}`)); });
    });
    return { output: `${result.matches.length} match(es) in ${label(ctx, root)}:\n${result.matches.join('\n')}${result.limited ? '\n[search limited; narrow scope]' : ''}`, metadata: { backend: 'node-worker' } };
  } finally { await worker.terminate(); }
}
export const searchTools: Tool[] = [
  { name: 'find', description: 'Search file/directory names by glob, up to 200 matches.', parameters: schema({ pattern: { type: 'string', minLength: 1 }, path: { type: 'string' }, type: { enum: ['any', 'file', 'dir'] } }, ['pattern']), run: find },
  { name: 'grep', description: 'Search UTF-8 content by JavaScript regex. Up to 200 matches, 300 characters/line, 2 MB/file and 30 seconds. Runs in a cancellable worker.', parameters: schema({ pattern: { type: 'string', minLength: 1 }, path: { type: 'string' }, glob: { type: 'string' }, ignore_case: { type: 'boolean' } }, ['pattern']), run: grep },
];
