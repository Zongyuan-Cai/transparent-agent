import { parseArgs } from 'node:util';
import { configFromEnv, expandHome, loadDotenv } from './config.js';
import { loadSnapshot } from './storage.js';
import type { RuntimeOptions } from './runtime.js';

export function argumentsFor(web = false, args = process.argv.slice(2)) {
  const { values } = parseArgs({ args, options: {
    workspace: { type: 'string', short: 'C' }, model: { type: 'string' }, 'base-url': { type: 'string' },
    'max-steps': { type: 'string' }, print: { type: 'string', short: 'p' }, resume: { type: 'string' },
    'state-dir': { type: 'string' }, 'trace-dir': { type: 'string' },
    'skill-dir': { type: 'string', multiple: true }, 'prompt-dir': { type: 'string', multiple: true },
    extension: { type: 'string', multiple: true }, inspect: { type: 'string' },
    version: { type: 'boolean' }, help: { type: 'boolean', short: 'h' },
    ...(web ? { host: { type: 'string' as const }, port: { type: 'string' as const }, token: { type: 'string' as const } } : {}),
  } });
  loadDotenv();
  const options: RuntimeOptions = { state_dir: values['state-dir'], trace_dir: values['trace-dir'], resume: values.resume,
    model_override: !!values.model, skill_dirs: values['skill-dir'], prompt_dirs: values['prompt-dir'], extensions: values.extension };
  const overrides = {
    ...(values.workspace ? { workspace: values.workspace } : values.resume ? { workspace: loadSnapshot(expandHome(values.resume)).workspace } : {}),
    ...(values.model ? { model: values.model } : {}), ...(values['base-url'] ? { base_url: values['base-url'] } : {}),
    ...(values['max-steps'] ? { max_steps: Number(values['max-steps']) } : {}),
  };
  return { values, options, config: values.help || values.version || values.inspect ? configFromEnv({}, {}) : configFromEnv(overrides) };
}
export const version = '0.2.0';
export const help = `Transparent Agent ${version}
Usage: transparent-agent [-C WORKSPACE] [-p PROMPT] [--resume SESSION.json]
       transparent-agent-web [-C WORKSPACE] [--host 127.0.0.1] [--port 8765]
Options: --model NAME --base-url URL --max-steps N --state-dir DIR --trace-dir DIR
         --skill-dir DIR --prompt-dir DIR --extension ESM_FILE --inspect TRACE.jsonl
         --version --help
Terminal commands: /help /clear /status /model [NAME] /skills /trace /exit
Use /skill:NAME or /prompt:NAME; end a line with \\ for multiline input.
`;
