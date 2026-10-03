import { existsSync, realpathSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { homedir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import type { Model, EventSink, Tool } from './types.js';
import { PersistenceError } from './types.js';
import type { Config } from './config.js';
import { expandHome } from './config.js';
import { EventBus } from './events.js';
import { SessionStore, TraceRecorder, repairPending } from './storage.js';
import { buildRegistry } from './tools/index.js';
import { Resources, buildContext } from './resources.js';
import { ChatModel } from './provider.js';
import { Agent } from './agent.js';

export interface RuntimeOptions { state_dir?: string; trace_dir?: string; resume?: string; model_override?: boolean; skill_dirs?: string[]; prompt_dirs?: string[]; extensions?: string[]; home?: string }
export class Runtime {
  config: Config; agent!: Agent; store!: SessionStore; recorder!: TraceRecorder; bus!: EventBus; resources!: Resources; model?: Model;
  session_id!: string; session_path!: string; trace_path!: string; private closed = false;
  private constructor(config: Config) { this.config = config; }
  static async create(config: Config, options: RuntimeOptions = {}, observer?: EventSink, model?: Model): Promise<Runtime> {
    const runtime = new Runtime({ ...config });
    try {
      const state = resolve(expandHome(options.state_dir || process.env.TRANSPARENT_AGENT_STATE_DIR || join(homedir(), '.local/state/transparent-agent')));
      let saved;
      if (options.resume) {
        runtime.store = new SessionStore(resolve(expandHome(options.resume))); saved = runtime.store.load();
        if (realpathSync(saved.workspace) !== config.workspace) throw new PersistenceError('session workspace does not match original workspace');
        if (options.trace_dir) throw new PersistenceError('cannot relocate an existing trace');
        runtime.session_id = saved.session_id; runtime.trace_path = saved.trace_path;
        if (!existsSync(runtime.trace_path)) throw new PersistenceError('session trace is missing');
        if (!options.model_override) runtime.config.model = saved.model;
      } else {
        runtime.session_id = randomUUID().replaceAll('-', ''); const directory = join(state, 'sessions', runtime.session_id);
        runtime.store = new SessionStore(join(directory, 'session.json'));
        runtime.trace_path = join(options.trace_dir ? resolve(expandHome(options.trace_dir), runtime.session_id) : directory, 'trace.jsonl');
      }
      runtime.session_path = runtime.store.path; runtime.recorder = new TraceRecorder(runtime.trace_path, runtime.session_id);
      runtime.bus = new EventBus(runtime.session_id, runtime.recorder, observer ? [observer] : [], runtime.recorder.seq);
      runtime.bus.publish(saved ? 'session_resumed' : 'session_start', { workspace: config.workspace, model: runtime.config.model,
        session_path: runtime.session_path, trace_path: runtime.trace_path, config: { max_steps: config.max_steps, max_output_chars: config.max_output_chars, model_timeout_s: config.request_timeout, bash_timeout_s: config.bash_timeout } });
      runtime.resources = new Resources(config.workspace, { ...options, bus: runtime.bus });
      const registry = buildRegistry(); if (runtime.resources.skills.size) registry.register(runtime.resources.tool());
      for (const raw of options.extensions || []) {
        const path = resolve(config.workspace, expandHome(raw));
        if (!/\.(m?js|ts)$/.test(path)) throw new Error('extensions must be ESM JavaScript/TypeScript modules; Python extensions require porting');
        const module = await import(pathToFileURL(path).href) as { register(api: { registerTool(tool: Tool): void; subscribe(observer: EventSink): void }): void | Promise<void> };
        const before = new Set(registry.names());
        await module.register({ registerTool: (tool) => registry.register(tool), subscribe: (sink) => { if (typeof sink.emit !== 'function') throw new Error('observer must expose emit(event)'); runtime.bus.observers.push(sink); } });
        runtime.bus.publish('extension_loaded', { path, tools: registry.names().filter((name) => !before.has(name)) });
      }
      const system = saved?.system ?? buildContext(config.workspace, runtime.resources);
      runtime.bus.publish('context_loaded', { source: saved ? 'session' : 'assembled_system', content: system });
      runtime.model = model || new ChatModel(runtime.config, runtime.bus);
      runtime.agent = new Agent({ model: runtime.model, tools: registry, system, bus: runtime.bus, max_steps: config.max_steps, resources: runtime.resources,
        context: { workspace: config.workspace, bash_timeout: config.bash_timeout, max_bash_timeout: config.max_bash_timeout, max_output_chars: config.max_output_chars }, save: () => runtime.save() });
      if (saved) {
        runtime.agent.history = saved.messages; const repaired = repairPending(runtime.agent.history);
        if (repaired.length) runtime.bus.publish('session_repaired', { call_ids: repaired, message: 'unknown execution state; no tools replayed' });
        if (saved.model !== runtime.model.name) runtime.bus.publish('model_changed', { previous: saved.model, model: runtime.model.name });
      }
      runtime.save(); return runtime;
    } catch (error) { await runtime.close(); throw error; }
  }
  save(): void { this.store.save({ schema_version: 1, session_id: this.session_id, workspace: this.config.workspace,
    model: this.model!.name, system: this.agent.system, messages: this.agent.history, trace_path: this.trace_path }); }
  async close(): Promise<void> {
    if (this.closed) return; this.closed = true;
    try { await this.model?.close?.(); } finally { try { this.recorder?.close(); } finally { this.store?.close(); } }
  }
}
