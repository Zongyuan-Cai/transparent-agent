import { mkdir, open, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { createPatch } from 'diff';
import type { Data, Tool, ToolContext, ToolResult } from '../types.js';
import { ToolError } from '../types.js';
import { label, workspacePath } from './base.js';

export const schema = (properties: Data, required: string[] = []): Data => ({ type: 'object', properties, required, additionalProperties: false });
const string = { type: 'string' };
export function text(bytes: Uint8Array, stream = false): string {
  if (bytes.subarray(0, 8192).includes(0)) throw new ToolError('file is binary; text tools cannot read it');
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes, { stream }); }
  catch { throw new ToolError('file is not valid UTF-8'); }
}
export async function read(args: Data, ctx: ToolContext): Promise<ToolResult> {
  const path = workspacePath(ctx, args.path), name = label(ctx, path), info = await stat(path);
  if (!info.isFile()) throw new ToolError('path is a directory; use ls');
  const handle = await open(path, 'r'); const data = Buffer.alloc(Math.min(info.size, 5_000_001));
  let count = 0;
  try { while (count < data.length) { const r = await handle.read(data, count, data.length - count, null); if (!r.bytesRead) break; count += r.bytesRead; } }
  finally { await handle.close(); }
  const clipped = count > 5_000_000;
  const content = text(data.subarray(0, Math.min(count, 5_000_000)), clipped);
  const lines = content.split(/\r\n|\n|\r/); if (lines.at(-1) === '' || clipped) lines.pop();
  if (!lines.length) return { output: `${name} is empty (0 bytes)` };
  const start = Number(args.start_line ?? 1), end = Math.min(Number(args.end_line ?? (args.start_line == null ? 2000 : lines.length)), lines.length);
  if (start < 1 || start > lines.length || end < start) throw new ToolError('invalid start_line/end_line or range beyond accessible lines');
  return { output: `${name} (lines ${start}-${end}${clipped ? '' : ` of ${lines.length}`})\n`
    + (clipped ? '[file exceeds 5 MB; only a complete-line prefix is available]\n' : '')
    + (end < lines.length ? '[use start_line/end_line to read further lines]\n' : '')
    + lines.slice(start - 1, end).map((line, i) => `${String(start + i).padStart(6)}\t${line}`).join('\n') };
}
export async function write(args: Data, ctx: ToolContext): Promise<ToolResult> {
  const path = workspacePath(ctx, args.path); await mkdir(dirname(path), { recursive: true });
  // Recheck after parent creation to catch an existing symlinked parent.
  workspacePath(ctx, args.path); const content = String(args.content);
  await writeFile(path, content, 'utf8');
  return { output: `Wrote ${label(ctx, path)}: ${Buffer.byteLength(content)} bytes` };
}
export async function edit(args: Data, ctx: ToolContext): Promise<ToolResult> {
  const path = workspacePath(ctx, args.path), original = text(await readFile(path));
  const old = String(args.old_string), replacement = String(args.new_string);
  if (!old || old === replacement) throw new ToolError('old_string must be non-empty and different from new_string');
  let source = original, normalized = false;
  if (!source.includes(old) && original.includes('\r\n') && !original.replaceAll('\r\n', '').includes('\n')) {
    source = original.replaceAll('\r\n', '\n'); normalized = true;
  }
  const matches = source.split(old).length - 1;
  if (!matches) throw new ToolError('old_string not found; match exact content without read line numbers');
  if (matches > 1 && !args.replace_all) throw new ToolError(`old_string appears ${matches} times; use unique context or replace_all=true`);
  let updated = args.replace_all ? source.split(old).join(replacement) : source.replace(old, () => replacement);
  if (normalized) updated = updated.replaceAll('\n', '\r\n');
  workspacePath(ctx, args.path); await writeFile(path, updated);
  const diff = createPatch(label(ctx, path), original, updated, '', '', { context: 2 }).split('\n');
  return { output: `Edited ${label(ctx, path)}: ${args.replace_all ? matches : 1} replacement(s)\n${diff.slice(0, 80).join('\n')}${diff.length > 80 ? '\n[diff truncated]' : ''}` };
}
export async function ls(args: Data, ctx: ToolContext): Promise<ToolResult> {
  const path = workspacePath(ctx, args.path || '.'), info = await stat(path);
  if (info.isFile()) return { output: `${label(ctx, path)} is a file (${info.size} bytes)` };
  const entries = await readdir(path, { withFileTypes: true });
  entries.sort((a, b) => Number(b.isDirectory()) - Number(a.isDirectory()) || a.name.localeCompare(b.name));
  return { output: `${label(ctx, path)}/ (${entries.length} entries)\n${entries.slice(0, 200).map((e) => e.name + (e.isDirectory() ? '/' : '')).join('\n')}${entries.length > 200 ? '\n[showing first 200 entries; use find]' : ''}` };
}
export const fileTools: Tool[] = [
  { name: 'read', description: 'Read UTF-8 text with line numbers; up to 5 MB, default first 2000 lines.', parameters: schema({ path: string, start_line: { type: 'integer' }, end_line: { type: 'integer' } }, ['path']), run: read },
  { name: 'write', description: 'Create or fully replace a file. Prefer edit for partial changes.', parameters: schema({ path: string, content: string }, ['path', 'content']), run: write },
  { name: 'edit', description: 'Replace an exact unique string; replace_all explicitly permits multiple matches.', parameters: schema({ path: string, old_string: string, new_string: string, replace_all: { type: 'boolean' } }, ['path', 'old_string', 'new_string']), run: edit },
  { name: 'ls', description: 'List one directory level (up to 200 entries).', parameters: schema({ path: string }), run: ls },
];
