import { createInterface } from 'node:readline';
import { argumentsFor, help, version } from './arguments.js';
import { Runtime } from './runtime.js';
import { Renderer } from './tui/render.js';
import { readTrace } from './storage.js';
import { errorText } from './types.js';

export async function main(args = process.argv.slice(2)): Promise<number> {
  let runtime: Runtime | undefined, input: ReturnType<typeof createInterface> | undefined, active: AbortController | undefined;
  const interrupt = () => { if (active) active.abort(); else if (input) { input.write(null, { ctrl: true, name: 'u' }); process.stdout.write('\n'); input.prompt(); } };
  try {
    const { values, config, options } = argumentsFor(false, args);
    if (values.help) { process.stdout.write(help); return 0; }
    if (values.version) { process.stdout.write(version + '\n'); return 0; }
    if (values.inspect) { for (const event of readTrace(values.inspect)) process.stdout.write(JSON.stringify(event, null, 2) + '\n'); return 0; }
    runtime = await Runtime.create(config, options, new Renderer(values.print !== undefined));
    process.on('SIGINT', interrupt);
    const paths = `session: ${runtime.session_path}\ntrace: ${runtime.trace_path}\n`;
    if (values.print !== undefined) {
      process.stderr.write(paths); active = new AbortController(); const result = await runtime.agent.send(values.print, active.signal);
      return result.stop_reason === 'final' ? 0 : result.stop_reason === 'cancelled' ? 130 : 1;
    }
    process.stdout.write(`Transparent Agent ${version}\n${config.workspace} · ${runtime.model!.name}\ntools: ${runtime.agent.tools.names().join(', ')}\n/help for commands\n${paths}\n`);
    input = createInterface({ input: process.stdin, output: process.stdout, terminal: !!process.stdin.isTTY, prompt: '> ' });
    input.on('SIGINT', interrupt); const queue: string[] = [];
    let ended = false, wake: (() => void) | undefined, multiline: string[] = [];
    // Readline's async iterator can drop lines while the agent is awaited. Queue every line explicitly.
    input.on('line', (line) => { queue.push(line); wake?.(); }); input.on('close', () => { ended = true; wake?.(); }); input.prompt();
    while (!ended || queue.length) {
      if (!queue.length) { await new Promise<void>((resolve) => { wake = resolve; }); wake = undefined; continue; }
      const raw = queue.shift()!;
      if (raw.trimEnd().endsWith('\\')) { multiline.push(raw.trimEnd().slice(0, -1)); input.setPrompt('… '); input.prompt(); continue; }
      const text = [...multiline, raw].join('\n').trim(); multiline = []; input.setPrompt('> ');
      if (!text) { input.prompt(); continue; }
      const [command, ...rest] = text.split(/\s+/);
      if (['/exit', '/quit'].includes(command)) break;
      if (command === '/help') process.stdout.write(help);
      else if (command === '/clear') runtime.agent.clear();
      else if (command === '/trace') process.stdout.write(paths);
      else if (command === '/status') process.stdout.write(`${runtime.model!.name} · ${runtime.agent.history.length} messages · ${runtime.agent.total_usage.total_tokens} tokens\n`);
      else if (command === '/model') { if (rest.length) runtime.agent.setModel(rest.join(' ')); process.stdout.write(runtime.model!.name + '\n'); }
      else if (command === '/skills') {
        for (const skill of runtime.resources.skills.values()) process.stdout.write(`/skill:${skill.name} — ${skill.description}\n`);
        for (const name of runtime.resources.prompts.keys()) process.stdout.write(`/prompt:${name}\n`);
      } else {
        active = new AbortController(); await runtime.agent.send(text, active.signal); active = undefined;
      }
      if (!ended) input.prompt();
    }
    return 0;
  } catch (error) { process.stderr.write(`transparent-agent: ${errorText(error)}\n`); return 2; }
  finally { process.off('SIGINT', interrupt); input?.close(); await runtime?.close(); }
}
