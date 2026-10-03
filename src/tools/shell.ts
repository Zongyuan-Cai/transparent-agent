import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { stat } from 'node:fs/promises';
import type { Data, Tool, ToolContext, ToolResult } from '../types.js';
import { ToolError, ToolInterrupted, Cancelled } from '../types.js';
import { label, workspacePath } from './base.js';
import { schema } from './files.js';

export async function bash(args: Data, ctx: ToolContext): Promise<ToolResult> {
  if (process.platform === 'win32') throw new ToolError('bash process groups require POSIX; run in WSL');
  if (ctx.signal.aborted) throw new Cancelled();
  const cwd = args.cwd ? workspacePath(ctx, args.cwd) : ctx.workspace;
  if (!(await stat(cwd)).isDirectory()) throw new ToolError('cwd must be a directory');
  const requested = Number(args.timeout_s ?? ctx.bash_timeout);
  if (!Number.isFinite(requested) || requested <= 0) throw new ToolError('timeout_s must be finite and positive');
  const timeout = Math.min(requested, ctx.max_bash_timeout), started = performance.now();
  const child = spawn('/bin/sh', ['-c', String(args.command)], { cwd, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const stdout: Buffer[] = [], stderr: Buffer[] = [];
  child.stdout.on('data', (chunk: Buffer) => stdout.push(chunk)); child.stderr.on('data', (chunk: Buffer) => stderr.push(chunk));
  let timedOut = false, cancelled = false, killTimer: NodeJS.Timeout | undefined;
  const killGroup = (signal: NodeJS.Signals): void => {
    if (!child.pid) return;
    try { process.kill(-child.pid, signal); } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error; }
  };
  const terminate = (): void => { killGroup('SIGTERM'); killTimer ??= setTimeout(() => killGroup('SIGKILL'), 1000); };
  const abort = (): void => { cancelled = true; terminate(); };
  const timer = setTimeout(() => { timedOut = true; terminate(); }, timeout * 1000);
  ctx.signal.addEventListener('abort', abort, { once: true }); if (ctx.signal.aborted) abort();
  let code: number | null = null, signal: NodeJS.Signals | null = null;
  try {
    // 'exit' need not wait for background descendants to close inherited pipes.
    await new Promise<void>((resolve, reject) => {
      child.once('error', reject); child.once('exit', (c, s) => { code = c; signal = s; resolve(); });
    });
    killGroup('SIGTERM'); killGroup('SIGKILL');
    if (!child.stdout.readableEnded || !child.stderr.readableEnded) await Promise.race([
      new Promise<void>((resolve) => child.once('close', () => resolve())), delay(1500),
    ]);
  } finally {
    clearTimeout(timer); if (killTimer) clearTimeout(killTimer);
    ctx.signal.removeEventListener('abort', abort); killGroup('SIGKILL');
    child.stdout.destroy(); child.stderr.destroy();
  }
  const out = Buffer.concat(stdout), err = Buffer.concat(stderr), duration = (performance.now() - started) / 1000;
  const status = cancelled ? 'cancelled' : timedOut ? 'timed_out' : 'completed';
  const exit = signal ? `killed by signal ${signal}` : String(code);
  const output = `status: ${status}\nexit_code: ${exit}\nduration: ${duration.toFixed(2)}s\ncwd: ${label(ctx, cwd)}\n`
    + (requested > timeout ? `[timeout_s ${requested} clamped to ${timeout}s]\n` : '')
    + `--- stdout ---\n${out.toString('utf8') || '(empty)'}\n--- stderr ---\n${err.toString('utf8') || '(empty)'}`;
  const result: ToolResult = { output, is_error: cancelled || timedOut || code !== 0, display: `${status === 'completed' && code === 0 ? '' : `[${status}; exit ${exit}]\n`}${out.toString()}${err.toString()}` || '(no output)',
    metadata: { stdout: out.toString(), stderr: err.toString(), stdout_base64: out.toString('base64'), stderr_base64: err.toString('base64'),
      exit_code: code, signal, elapsed: duration, timeout: timedOut, cancelled } };
  if (cancelled) throw new ToolInterrupted(result);
  return result;
}
export const shellTools: Tool[] = [{ name: 'bash', description: 'Run a non-interactive shell command. Returns both streams, exit status and duration; cleans the whole process group.',
  parameters: schema({ command: { type: 'string', minLength: 1 }, cwd: { type: 'string' }, timeout_s: { type: 'number', exclusiveMinimum: 0 } }, ['command']), run: bash }];
