import { randomUUID } from 'node:crypto';
import type { Message, Model, StreamItem, ToolContext, TurnResult, Usage } from './types.js';
import { Cancelled, ModelError, ToolInterrupted, PersistenceError, addUsage, errorText, zeroUsage } from './types.js';
import { ToolRegistry, bound } from './tools/index.js';
import { EventBus } from './events.js';
import { repairPending } from './storage.js';
import { Resources } from './resources.js';

export class Agent {
  history: Message[] = []; total_usage = zeroUsage(); running = false;
  model: Model; tools: ToolRegistry; context: Omit<ToolContext, 'signal'>; system: string; bus: EventBus;
  resources?: Resources; max_steps: number; save: () => void;
  constructor(options: { model: Model; tools: ToolRegistry; context: Omit<ToolContext, 'signal'>; system: string; bus: EventBus; max_steps: number; save?: () => void; resources?: Resources }) {
    this.model = options.model; this.tools = options.tools; this.context = options.context; this.system = options.system;
    this.bus = options.bus; this.max_steps = options.max_steps; this.save = options.save || (() => {}); this.resources = options.resources;
  }
  clear(): void { if (this.running) throw new Error('cannot clear active agent'); this.history = []; this.save(); this.bus.publish('session_cleared', {}); }
  setModel(name: string): void {
    if (this.running || !name.trim()) throw new Error('cannot change model while running or to an empty name');
    const previous = this.model.name; this.model.name = name; this.save(); this.bus.publish('model_changed', { previous, model: name });
  }
  async send(input: string, signal = new AbortController().signal): Promise<TurnResult> {
    if (this.running) throw new Error('agent is already running'); this.running = true;
    this.bus.beginRun(); let steps = 0, usage: Usage = zeroUsage(); const started = performance.now();
    const finish = (stop_reason: TurnResult['stop_reason'], text = '', error: string | null = null): TurnResult => {
      const result = { text, stop_reason, steps, usage, error };
      this.save(); this.bus.publish('agent_finish', { ...result, elapsed_s: (performance.now() - started) / 1000 }); return result;
    };
    try {
      if (signal.aborted) throw new Cancelled();
      this.bus.publish('agent_start', { model: this.model.name, workspace: this.context.workspace, max_steps: this.max_steps });
      try { input = this.resources?.expand(input) ?? input; }
      catch (error) { this.bus.publish('agent_error', { stage: 'resource', message: errorText(error) }); return finish('error', '', errorText(error)); }
      this.history.push({ role: 'user', content: input }); this.bus.publish('user_message', { text: input }); this.save();
      for (let step = 1; step <= this.max_steps; step++) {
        if (signal.aborted) throw new Cancelled(); steps = step;
        const request_id = this.bus.request_id = randomUUID().replaceAll('-', ''), tools = this.tools.specs();
        this.bus.publish('model_request_start', { step, request_id, system: this.system, messages: this.history.map((m) => ({ ...m })), tools: tools.map((t) => ({ ...t })) });
        let response: Extract<StreamItem, { type: 'response' }> | undefined;
        try {
          for await (const item of this.model.stream({ system: this.system, messages: structuredClone(this.history), tools: structuredClone(tools), signal })) {
            if (signal.aborted) throw new Cancelled();
            if (item.type === 'response') { if (response) throw new ModelError('more than one complete response'); response = item; }
            else this.bus.publish(item.type === 'text' ? 'model_text_delta' : 'model_reasoning_delta', { step, text: item.text });
          }
          if (!response) throw new ModelError('model stream ended without a complete response');
        } catch (error) {
          if (error instanceof Cancelled || signal.aborted || error instanceof PersistenceError) throw error;
          this.bus.publish('agent_error', { stage: 'model_request', step, message: errorText(error) }); return finish('error', '', errorText(error));
        }
        if (response.usage) { usage = addUsage(usage, response.usage); this.total_usage = addUsage(this.total_usage, response.usage); }
        const { type: ignored, ...data } = response;
        this.bus.publish('model_response', { ...data, step, request_id });
        this.history.push({ role: 'assistant', content: response.text || null, reasoning: response.reasoning || null, tool_calls: response.tool_calls }); this.save();
        if (!response.tool_calls.length) return finish('final', response.text);
        let interrupted = false;
        for (const call of response.tool_calls) {
          this.bus.publish('tool_call_start', { step, call_id: call.id, name: call.name, arguments: call.arguments }); const before = performance.now();
          let result;
          if (interrupted || signal.aborted) { interrupted = true; result = { output: 'cancelled before execution', is_error: true }; }
          else try { result = await this.tools.execute(call.name, call.arguments, { ...this.context, signal }); }
          catch (error) {
            if (error instanceof PersistenceError) throw error;
            if (!(error instanceof Cancelled) && !signal.aborted) throw error;
            interrupted = true; result = bound(error instanceof ToolInterrupted ? error.result : { output: 'cancelled', is_error: true }, this.context.max_output_chars);
          }
          const model_content = (result.is_error ? 'Error: ' : '') + result.output;
          this.history.push({ role: 'tool', tool_call_id: call.id, content: model_content });
          this.bus.publish('tool_call_end', { step, call_id: call.id, name: call.name, output: result.output,
            full_output: result.raw_output ?? result.output, model_content, metadata: result.metadata || {}, display: result.display || result.output,
            is_error: !!result.is_error, cancelled: interrupted, truncated: !!result.raw_output && result.raw_output !== result.output, duration_s: (performance.now() - before) / 1000 }); this.save();
        }
        if (interrupted || signal.aborted) return finish('cancelled');
      }
      return finish('max_steps', '', `reached step limit (${this.max_steps})`);
    } catch (error) {
      if (error instanceof PersistenceError) throw error;
      if (!(error instanceof Cancelled) && !signal.aborted) throw error;
      repairPending(this.history, 'Error: cancelled; tool not replayed.'); return finish('cancelled');
    } finally { this.running = false; this.bus.endRun(); }
  }
}
