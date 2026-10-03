import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { configFromEnv } from '../dist/config.js';
import type { Model, StreamItem, Message, ToolSpec } from '../src/types.js';
export const answer = (text = 'done'): Extract<StreamItem, { type: 'response' }> => ({ type: 'response', text, reasoning: '', tool_calls: [], finish_reason: 'stop', usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 } });
export const calls = (...items: { id: string; name: string; arguments: string }[]) => ({ ...answer(''), tool_calls: items, finish_reason: 'tool_calls' });
export class FakeModel implements Model {
  name = 'fake'; requests: { system: string; messages: Message[]; tools: ToolSpec[] }[] = []; turns: StreamItem[];
  constructor(turns: StreamItem[] = []) { this.turns = turns; }
  async *stream(input: Parameters<Model['stream']>[0]) { this.requests.push(structuredClone({ system: input.system, messages: input.messages, tools: input.tools })); const turn = this.turns.shift(); if (!turn) throw new Error('no fake response'); if (turn.type === 'response' && turn.text) yield { type: 'text' as const, text: turn.text }; yield turn; }
}
export function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'transparent-test-')), workspace = join(root, 'project'); mkdirSync(workspace); mkdirSync(join(workspace, '.git'));
  return { root, workspace, options: { state_dir: join(root, 'state'), home: join(root, 'home') }, config: configFromEnv({ workspace, model: 'fake', api_key: 'test-secret', max_output_chars: 500 }), clean: () => rmSync(root, { recursive: true, force: true }) };
}
export const waitUntil = async (predicate: () => boolean | Promise<boolean>, timeout = 5000) => {
  const until = Date.now() + timeout;
  while (!(await predicate())) { if (Date.now() > until) throw new Error('condition timed out'); await new Promise((r) => setTimeout(r, 20)); }
};
