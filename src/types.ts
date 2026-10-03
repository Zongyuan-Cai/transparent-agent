export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Data = Record<string, Json>;
export interface ToolCall { id: string; name: string; arguments: string }
export interface Message {
  role: 'user' | 'assistant' | 'tool'; content: string | null;
  tool_calls?: ToolCall[]; tool_call_id?: string; reasoning?: string | null;
}
export interface ToolSpec { name: string; description: string; parameters: Data }
export interface Usage { prompt_tokens: number; completion_tokens: number; total_tokens: number }
export const zeroUsage = (): Usage => ({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 });
export const addUsage = (a: Usage, b: Usage): Usage => ({ prompt_tokens: a.prompt_tokens + b.prompt_tokens,
  completion_tokens: a.completion_tokens + b.completion_tokens, total_tokens: a.total_tokens + b.total_tokens });
export type StreamItem = { type: 'text' | 'reasoning'; text: string } | {
  type: 'response'; text: string; reasoning: string; tool_calls: ToolCall[]; finish_reason: string | null; usage: Usage | null;
};
export interface Model {
  name: string;
  stream(input: { system: string; messages: Message[]; tools: ToolSpec[]; signal: AbortSignal }): AsyncGenerator<StreamItem>;
  close?(): void | Promise<void>;
}
export interface ToolResult { output: string; is_error?: boolean; display?: string; raw_output?: string; metadata?: Data }
export interface ToolContext { workspace: string; bash_timeout: number; max_bash_timeout: number; max_output_chars: number; signal: AbortSignal }
export interface Tool extends ToolSpec { run(args: Data, context: ToolContext): ToolResult | Promise<ToolResult> }
export interface Event { schema_version: 1; session_id: string; run_id: string | null; seq: number; type: string; timestamp: string; data: Data }
export interface EventSink { emit(event: Event): void }
export type StopReason = 'final' | 'cancelled' | 'max_steps' | 'error';
export interface TurnResult { text: string; stop_reason: StopReason; steps: number; usage: Usage; error: string | null }
export class PersistenceError extends Error {}
export class ToolError extends Error {}
export class ModelError extends Error {}
export class Cancelled extends Error { constructor() { super('cancelled'); } }
export class ToolInterrupted extends Cancelled { result: ToolResult; constructor(result: ToolResult) { super(); this.result = result; } }
export const errorText = (error: unknown): string => error instanceof Error ? error.message : String(error);
export const isCancelled = (error: unknown, signal: AbortSignal): boolean => signal.aborted || error instanceof Cancelled;
