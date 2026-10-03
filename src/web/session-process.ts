import { Runtime } from '../runtime.js';
import type { Config } from '../config.js';
import type { RuntimeOptions } from '../runtime.js';
import { errorText } from '../types.js';

let runtime: Runtime | undefined, active: AbortController | undefined, shutdown = false;
const send = (message: object) => { if (process.connected) process.send?.(message); };
async function close() { if (active) { shutdown = true; active.abort(); return; } await runtime?.close(); process.disconnect?.(); }
process.on('disconnect', () => { void close(); }); process.on('SIGTERM', () => { void close(); });
process.on('message', (raw) => { void handle(raw).catch(async (error) => { send({ kind: 'error', error: errorText(error) }); await close(); }); });
async function handle(raw: unknown) {
  const command = raw as { kind: string; config: Config; options: RuntimeOptions; text: string };
  if (command.kind === 'init') {
    if (runtime) throw new Error('already initialized');
    runtime = await Runtime.create(command.config, command.options, { emit: (event) => send({ kind: 'event', seq: event.seq }) });
    send({ kind: 'ready', session_id: runtime.session_id, trace_path: runtime.trace_path, resources: {
      skills: [...runtime.resources.skills.values()].map(({ name, description }) => ({ name, description })),
      prompts: [...runtime.resources.prompts.keys()], tools: runtime.agent.tools.names(),
    } }); return;
  }
  if (command.kind === 'shutdown') { await close(); return; }
  if (command.kind === 'cancel') { active?.abort(); return; }
  if (!runtime || active) throw new Error('worker is not idle');
  if (command.kind === 'clear') { runtime.agent.clear(); send({ kind: 'result', result: null }); }
  else if (command.kind === 'send') {
    active = new AbortController();
    try { const result = await runtime.agent.send(command.text, active.signal); send({ kind: 'result', result }); }
    finally { active = undefined; if (shutdown) await close(); }
  }
}
