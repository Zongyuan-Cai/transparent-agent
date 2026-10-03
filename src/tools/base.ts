import { existsSync, lstatSync, realpathSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { expandHome } from '../config.js';
import type { ToolContext, ToolResult } from '../types.js';
import { ToolError } from '../types.js';

export function realPath(path: string): string {
  if (existsSync(path)) return realpathSync(path);
  // Dangling symlinks must not turn into apparently safe new-file paths.
  try { if (lstatSync(path).isSymbolicLink()) return realpathSync(path); } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    try { if (lstatSync(path).isSymbolicLink()) throw new ToolError('dangling symlink'); } catch (nested) {
      if (nested instanceof ToolError) throw nested;
    }
  }
  const parent = dirname(path);
  return parent === path ? path : join(realPath(parent), path.slice(parent.length + 1));
}
export function inside(root: string, path: string): boolean {
  const r = relative(root, path); return r === '' || (r !== '..' && !r.startsWith('../') && !isAbsolute(r));
}
export function workspacePath(ctx: Pick<ToolContext, 'workspace'>, raw: unknown): string {
  if (typeof raw !== 'string' || !raw.trim()) throw new ToolError('path must be a non-empty string');
  const root = realpathSync(ctx.workspace), path = realPath(resolve(root, expandHome(raw)));
  if (!inside(root, path)) throw new ToolError(`path ${JSON.stringify(raw)} is outside the workspace`);
  return path;
}
export const label = (ctx: Pick<ToolContext, 'workspace'>, path: string): string => relative(ctx.workspace, path) || '.';
export function truncateMiddle(text: string, limit: number): string {
  if (text.length <= limit) return text;
  if (limit < 48) return '[truncated]'.slice(0, limit);
  const budget = limit - `\n... [${text.length} characters omitted] ...\n`.length;
  const head = Math.floor(budget * .6), tail = budget - head;
  return `${text.slice(0, head)}\n... [${text.length - budget} characters omitted] ...\n${text.slice(-tail)}`;
}
export const bound = (result: ToolResult, limit: number): ToolResult => result.output.length <= limit ? result :
  { ...result, raw_output: result.raw_output ?? result.output, output: truncateMiddle(result.output, limit) };
