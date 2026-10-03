import { fork } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { homedir } from 'node:os';
import { EventEmitter } from 'node:events';
import { fileURLToPath } from 'node:url';
import { configFromEnv, expandHome } from '../config.js';
import type { Config } from '../config.js';
import type { RuntimeOptions } from '../runtime.js';
import { loadSnapshot } from '../storage.js';
import { Resources } from '../resources.js';
import { EventBus } from '../events.js';
import { errorText } from '../types.js';
import type { TurnResult } from '../types.js';
import { TraceView } from './trace-view.js';

export class WebError extends Error { status: number; constructor(message: string, status = 400) { super(message); this.status = status; } }
interface Catalog { skills: { name: string; description: string }[]; prompts: string[]; tools: string[] }
class Worker {
  status = 'starting'; error: string | null = null; result: TurnResult | null = null;
  session_id = ''; trace_path = ''; resources: Catalog = { skills: [], prompts: [], tools: [] };
  last_used = performance.now(); process: ChildProcess; closing = false; finished = new EventEmitter();
  private changed: () => void;
  private constructor(changed: () => void) {
    this.changed = changed;
    this.process = fork(fileURLToPath(new URL('./session-process.js', import.meta.url)), [], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
    let stderr = ''; this.process.stderr?.on('data', (bytes: Buffer) => { stderr = (stderr + bytes.toString()).slice(-4000); });
    const failed = (message: string) => {
      if (!this.closing && this.status !== 'failed') { this.status = 'failed'; this.error = message; }
      this.finished.emit('change'); changed();
    };
    this.process.on('error', (error) => failed(errorText(error)));
    this.process.on('exit', () => failed(stderr || 'session worker exited; resume from the saved session'));
    this.process.on('message', (raw) => {
      const message = raw as { kind: string; session_id: string; trace_path: string; resources: Catalog; result: TurnResult | null; error: string };
      if (message.kind === 'ready') { this.session_id = message.session_id; this.trace_path = message.trace_path; this.resources = message.resources; this.status = 'idle'; }
      else if (message.kind === 'result') { this.result = message.result; this.status = 'idle'; }
      else if (message.kind === 'error') { this.error = message.error; this.status = 'failed'; }
      this.finished.emit('change'); changed();
    });
  }
  static async create(config: Config, options: RuntimeOptions, changed: () => void) {
    const worker = new Worker(changed); worker.process.send({ kind: 'init', config, options });
    try { await worker.until(() => worker.status !== 'starting', 20000); if (worker.error) throw new WebError(worker.error, 409); return worker; }
    catch (error) { await worker.close(); throw error; }
  }
  until(done: () => boolean, timeout = 10000): Promise<void> {
    if (done()) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const onChange = () => { if (done()) { cleanup(); resolve(); } };
      const timer = setTimeout(() => { cleanup(); reject(new WebError('session worker timed out', 503)); }, timeout);
      const cleanup = () => { clearTimeout(timer); this.finished.off('change', onChange); }; this.finished.on('change', onChange);
    });
  }
  submit(kind: 'send' | 'clear', text?: string) {
    if (this.status !== 'idle') throw new WebError('session is already running or unavailable', 409);
    this.status = 'running'; this.result = null; this.last_used = performance.now();
    this.process.send({ kind, text }, (error) => { if (error) { this.error = errorText(error); this.status = 'failed'; this.finished.emit('change'); this.changed(); } }); this.changed();
  }
  cancel() { if (this.status === 'running') { this.status = 'cancelling'; this.process.send({ kind: 'cancel' }); this.changed(); } }
  async close() {
    this.closing = true; this.cancel();
    if (this.process.exitCode !== null || this.process.signalCode !== null) return;
    if (this.process.connected) this.process.send({ kind: 'shutdown' });
    const exited = () => this.process.exitCode !== null || this.process.signalCode !== null;
    try { await this.until(exited, 5000); } catch {
      this.process.kill('SIGTERM'); try { await this.until(exited, 5000); } catch { this.process.kill('SIGKILL'); await this.until(exited, 2000); }
    }
  }
}
export class SessionManager {
  state_dir: string; workers = new Map<string, Worker>(); views = new Map<string, TraceView>(); changed = new EventEmitter();
  private closed = false; private tail: Promise<unknown> = Promise.resolve();
  readonly config: Config; readonly options: RuntimeOptions; readonly max_workers: number;
  constructor(config: Config, options: RuntimeOptions = {}, max_workers = 8) {
    this.config = config; this.options = options; this.max_workers = max_workers;
    if (process.platform === 'win32') throw new WebError('run on Linux/macOS or inside WSL for process cancellation');
    this.state_dir = resolve(expandHome(options.state_dir || process.env.TRANSPARENT_AGENT_STATE_DIR || join(homedir(), '.local/state/transparent-agent')));
  }
  private serial<T>(action: () => Promise<T>): Promise<T> { const next = this.tail.then(action); this.tail = next.catch(() => {}); return next; }
  path(id: string) { if (!/^[0-9a-f]{32}$/.test(id)) throw new WebError('invalid session ID', 404); return join(this.state_dir, 'sessions', id, 'session.json'); }
  snapshot(id: string) {
    const path = this.path(id);
    try { const saved = loadSnapshot(path); if (saved.session_id !== id) throw new Error('invalid session identity'); return saved; }
    catch (error) { try { statSync(path); } catch { throw new WebError('session not found', 404); } throw new WebError(errorText(error), 409); }
  }
  status(id: string) { const w = this.workers.get(id); return { status: w?.status || 'idle', error: w?.error || null, result: w?.result || null }; }
  detail(id: string, includeResources = true) {
    const saved = this.snapshot(id), worker = this.workers.get(id); let resources: Catalog = worker?.resources || { skills: [], prompts: [], tools: [] };
    if (!worker && includeResources) {
      const bus = new EventBus(id, { emit() {} }); const catalog = new Resources(saved.workspace, { ...this.options, bus });
      resources = { skills: [...catalog.skills.values()].map(({ name, description }) => ({ name, description })), prompts: [...catalog.prompts.keys()], tools: [] };
    }
    return { session_id: saved.session_id, workspace: saved.workspace, model: saved.model, messages: saved.messages, ...this.status(id), resources };
  }
  list() {
    const result = []; let names: string[];
    try { names = readdirSync(join(this.state_dir, 'sessions')); } catch { return []; }
    for (const id of names) try {
      const item = this.detail(id, false); result.push({ session_id: id, workspace: item.workspace, model: item.model, status: item.status, error: item.error,
        title: (item.messages.find((m) => m.role === 'user')?.content || '新会话').slice(0, 100), updated_at: statSync(this.path(id)).mtimeMs / 1000 });
    } catch { /* Corrupt and unrelated directories do not hide valid sessions. */ }
    return result.sort((a, b) => b.updated_at - a.updated_at);
  }
  private async capacity() {
    if (this.closed) throw new WebError('server is shutting down', 503);
    if (this.workers.size < this.max_workers) return;
    const candidate = [...this.workers.values()].filter((w) => ['idle', 'failed'].includes(w.status)).sort((a, b) => a.last_used - b.last_used)[0];
    if (!candidate) throw new WebError('all session workers are busy', 503);
    await candidate.close(); this.workers.delete(candidate.session_id);
  }
  async create(workspace?: string, model?: string) { return this.serial(async () => {
    await this.capacity(); const config = configFromEnv({ ...this.config, workspace: workspace || this.config.workspace, model: model || this.config.model });
    const worker = await Worker.create(config, { ...this.options, resume: undefined, state_dir: this.state_dir }, () => this.changed.emit('change'));
    this.workers.set(worker.session_id, worker); return this.detail(worker.session_id);
  }); }
  private async activate(id: string) {
    let worker = this.workers.get(id); if (worker && worker.status !== 'failed') return worker;
    if (worker) { await worker.close(); this.workers.delete(id); }
    await this.capacity(); const saved = this.snapshot(id);
    worker = await Worker.create({ ...this.config, workspace: saved.workspace, model: saved.model }, { ...this.options, resume: this.path(id), trace_dir: undefined }, () => this.changed.emit('change'));
    this.workers.set(id, worker); return worker;
  }
  send(id: string, text: string) { return this.serial(async () => { (await this.activate(id)).submit('send', text); }); }
  cancel(id: string) { this.snapshot(id); this.workers.get(id)?.cancel(); }
  clear(id: string) { return this.serial(async () => {
    const worker = await this.activate(id); worker.submit('clear'); await worker.until(() => worker.status !== 'running'); if (worker.error) throw new WebError(worker.error, 409);
  }); }
  trace(id: string) { let view = this.views.get(id); if (!view) { view = new TraceView(this.snapshot(id).trace_path, id); this.views.set(id, view); } return view; }
  async close() { this.closed = true; await this.tail; this.changed.emit('change'); await Promise.all([...this.workers.values()].map((w) => w.close())); }
}
