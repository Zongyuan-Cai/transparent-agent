import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeFileSync, writeSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import fsExt from 'fs-ext';
import type { Event, Message } from './types.js';
import { PersistenceError, errorText } from './types.js';

export function privateDirectory(path: string): void { mkdirSync(path, { recursive: true, mode: 0o700 }); }
export function atomicJson(path: string, value: unknown): void {
  const temp = `${path}.${randomUUID()}.tmp`; let fd: number | undefined;
  try {
    privateDirectory(dirname(path)); fd = openSync(temp, 'wx', 0o600);
    writeFileSync(fd, JSON.stringify(value)); fsyncSync(fd); closeSync(fd); fd = undefined;
    renameSync(temp, path);
    const directory = openSync(dirname(path), 'r'); try { fsyncSync(directory); } finally { closeSync(directory); }
  } catch (error) { throw new PersistenceError(`cannot save ${path}: ${errorText(error)}`); }
  finally { if (fd !== undefined) closeSync(fd); if (existsSync(temp)) unlinkSync(temp); }
}
export function* readTrace(path: string): Generator<Event> {
  // Offline validation; the Web index reads records lazily by byte offset.
  let seq = 0, id: string | undefined;
  let content: string;
  try { content = readFileSync(path, 'utf8'); } catch (error) { throw new PersistenceError(`cannot read trace: ${errorText(error)}`); }
  if (content && !content.endsWith('\n')) throw new PersistenceError('trace has an incomplete trailing record');
  for (const line of content.split('\n')) {
    if (!line) continue;
    let record: Event;
    try { record = JSON.parse(line) as Event; } catch (error) { throw new PersistenceError(`invalid trace event ${seq + 1}: ${errorText(error)}`); }
    if (record.schema_version !== 1 || record.seq !== ++seq || !record.data || typeof record.data !== 'object'
      || Array.isArray(record.data) || typeof record.type !== 'string' || (id && id !== record.session_id)) throw new PersistenceError(`invalid trace event ${seq}`);
    id = record.session_id; yield record;
  }
}
export class TraceRecorder {
  path: string; seq = 0; fd: number;
  constructor(path: string, session_id: string) {
    this.path = path; privateDirectory(dirname(path));
    if (existsSync(path)) for (const record of readTrace(path)) {
      if (record.session_id !== session_id) throw new PersistenceError('trace belongs to another session');
      this.seq = record.seq;
    }
    this.fd = openSync(path, 'a', 0o600);
  }
  emit(event: Event): void {
    try {
      if (event.seq !== this.seq + 1) throw new Error('non-contiguous trace sequence');
      const bytes = Buffer.from(JSON.stringify(event) + '\n');
      let offset = 0;
      while (offset < bytes.length) { const count = writeSync(this.fd, bytes, offset, bytes.length - offset); if (!count) throw new Error('zero-length trace write'); offset += count; }
      this.seq = event.seq;
    } catch (error) { throw new PersistenceError(`cannot record trace: ${errorText(error)}`); }
  }
  close(): void { closeSync(this.fd); }
}
export interface Snapshot { schema_version: 1; session_id: string; workspace: string; model: string; system: string; messages: Message[]; trace_path: string }
export function loadSnapshot(path: string): Snapshot {
  try {
    const s = JSON.parse(readFileSync(path, 'utf8')) as Snapshot;
    if (s.schema_version !== 1 || !/^[0-9a-f]{32}$/.test(s.session_id) || typeof s.system !== 'string'
      || !['workspace', 'model', 'trace_path'].every((k) => typeof s[k as keyof Snapshot] === 'string') || !Array.isArray(s.messages)) throw new Error('invalid session');
    for (const message of s.messages) {
      if (!['user', 'assistant', 'tool'].includes(message.role) || (message.content !== null && typeof message.content !== 'string')
        || (message.reasoning != null && typeof message.reasoning !== 'string')
        || (message.tool_call_id != null && typeof message.tool_call_id !== 'string')) throw new Error('invalid message');
      if (message.tool_calls != null && !Array.isArray(message.tool_calls)) throw new Error('invalid tool calls');
      for (const call of message.tool_calls || []) if (!call || typeof call.id !== 'string' || !call.id || typeof call.name !== 'string' || !call.name || typeof call.arguments !== 'string') throw new Error('invalid tool call');
    }
    return s;
  } catch (error) { throw new PersistenceError(`cannot restore session: ${errorText(error)}`); }
}
export class SessionStore {
  path: string; fd: number;
  constructor(path: string) {
    this.path = path; privateDirectory(dirname(path)); this.fd = openSync(path.replace(/\.json$/, '.lock'), 'a', 0o600);
    try { fsExt.flockSync(this.fd, 'exnb'); }
    catch (error) { closeSync(this.fd); throw new PersistenceError(`session is locked or unavailable: ${errorText(error)}`); }
  }
  save(snapshot: Snapshot): void { atomicJson(this.path, snapshot); }
  load(): Snapshot { return loadSnapshot(this.path); }
  close(): void { closeSync(this.fd); }
}
export function repairPending(history: Message[], content = 'Error: execution state unknown after interruption; not replayed. Inspect the workspace before retrying.'): string[] {
  const pending = new Map<string, string>();
  for (const message of history) {
    if (pending.size && message.role !== 'tool') throw new PersistenceError('unanswered tool calls precede another message');
    for (const call of message.tool_calls || []) {
      if (pending.has(call.id)) throw new PersistenceError('duplicate pending tool call ID');
      pending.set(call.id, call.name);
    }
    if (message.role === 'tool') {
      if (!message.tool_call_id || !pending.delete(message.tool_call_id)) throw new PersistenceError('tool result has no matching call');
    }
  }
  for (const id of pending.keys()) history.push({ role: 'tool', tool_call_id: id, content });
  return [...pending.keys()];
}
