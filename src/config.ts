import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve, join } from 'node:path';

export interface Config {
  workspace: string; model: string; api_key?: string; base_url?: string;
  max_steps: number; bash_timeout: number; max_bash_timeout: number; max_output_chars: number;
  request_timeout: number; stream_usage: boolean;
}
export const expandHome = (path: string): string => path === '~' ? homedir() : path.startsWith('~/') ? join(homedir(), path.slice(2)) : path;
export function parseDotenv(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const match = raw.trim().replace(/^export\s+/, '').match(/^([^=#\s]+)\s*=\s*(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if (/^(['"]).*\1$/.test(value)) value = value.slice(1, -1);
    else value = value.split(' #')[0].trim();
    result[match[1]] = value;
  }
  return result;
}
export function loadDotenv(cwd = process.cwd(), env = process.env): void {
  for (const path of [join(cwd, '.env'), join(homedir(), '.config/transparent-agent/.env')]) {
    if (existsSync(path)) for (const [key, value] of Object.entries(parseDotenv(readFileSync(path, 'utf8')))) env[key] ??= value;
  }
}
export function configFromEnv(overrides: Partial<Config> = {}, env = process.env): Config {
  const num = (name: string, fallback: number): number => env[name] ? Number(env[name]) : fallback;
  const config: Config = {
    workspace: env.WORKSPACE_DIR || process.cwd(), model: env.OPENAI_MODEL || 'gpt-4o',
    api_key: env.OPENAI_API_KEY, base_url: env.OPENAI_BASE_URL, max_steps: num('MAX_STEPS', 40),
    bash_timeout: num('BASH_TIMEOUT_S', 120), max_bash_timeout: num('MAX_BASH_TIMEOUT_S', 600),
    max_output_chars: num('MAX_TOOL_OUTPUT_CHARS', 30000), request_timeout: num('MODEL_TIMEOUT_S', 300),
    stream_usage: env.STREAM_USAGE ? !['0', 'false', 'no', 'off'].includes(env.STREAM_USAGE.toLowerCase()) : true,
    ...overrides,
  };
  config.workspace = realpathSync(resolve(expandHome(config.workspace)));
  if (!statSync(config.workspace).isDirectory()) throw new Error('workspace must be a directory');
  if (!config.model.trim()) throw new Error('model must not be empty');
  for (const key of ['max_steps', 'bash_timeout', 'max_bash_timeout', 'max_output_chars', 'request_timeout'] as const) {
    if (!Number.isFinite(config[key]) || config[key] <= 0) throw new Error(`${key} must be finite and positive`);
  }
  if (!Number.isInteger(config.max_steps) || !Number.isInteger(config.max_output_chars)) throw new Error('steps and output limit must be integers');
  if (config.bash_timeout > config.max_bash_timeout) throw new Error('bash timeout exceeds maximum');
  if (config.base_url && !['http:', 'https:'].includes(new URL(config.base_url).protocol)) throw new Error('base URL must use http(s)');
  return config;
}
