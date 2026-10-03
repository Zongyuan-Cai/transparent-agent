import { closeSync, openSync, readSync, statSync } from 'node:fs';
import { gunzipSync, inflateSync, brotliDecompressSync } from 'node:zlib';
import type { Data, Event, Json } from '../types.js';
import { PersistenceError, errorText } from '../types.js';

export function summarize(record: Event): Event {
  const data: Data = {};
  for (const key of ['request_id', 'step', 'call_id', 'name', 'status', 'status_code', 'stop_reason', 'is_error', 'cancelled', 'duration_s', 'truncated', 'usage', 'error', 'error_type']) {
    if (key in record.data) data[key] = record.data[key];
  }
  for (const key of ['text', 'content', 'reasoning', 'output', 'message', 'arguments']) if (typeof record.data[key] === 'string') data[key] = record.data[key].slice(0, 8192);
  if (record.type === 'api_response_chunk') data.bytes = Buffer.from(String(record.data.body_base64), 'base64').length;
  if (Array.isArray(record.data.tool_calls)) data.tool_calls = record.data.tool_calls.map((c) => {
    const call = c as Record<string, Json>; return { id: call.id, name: call.name };
  });
  return { ...record, data };
}
export class TraceView {
  private offset = 0;
  private positions: [number, number][] = [];
  private summaries: Event[] = [];
  private requests = new Map<string, number[]>();
  readonly path: string; readonly session_id: string;
  constructor(path: string, session_id: string) { this.path = path; this.session_id = session_id; }
  refresh(): void {
    if (statSync(this.path).size < this.offset) throw new PersistenceError('trace was truncated while reading');
    const fd = openSync(this.path, 'r'); let position = this.offset, carry = Buffer.alloc(0);
    try {
      const block = Buffer.alloc(128 * 1024); let count;
      while ((count = readSync(fd, block, 0, block.length, position)) > 0) {
        position += count; const bytes = Buffer.concat([carry, block.subarray(0, count)]); let start = 0, end;
        while ((end = bytes.indexOf(10, start)) !== -1) {
          const length = end - start + 1;
          try {
            const record = JSON.parse(bytes.subarray(start, end).toString('utf8')) as Event;
            if (record.schema_version !== 1 || record.seq !== this.positions.length + 1 || record.session_id !== this.session_id
              || !record.data || typeof record.data !== 'object' || Array.isArray(record.data) || typeof record.type !== 'string') throw new Error('invalid trace identity, version or sequence');
            const summary = summarize(record); this.positions.push([this.offset, length]); this.summaries.push(summary);
            const request_id = record.data.request_id;
            if (typeof request_id === 'string') { const list = this.requests.get(request_id) || []; list.push(record.seq); this.requests.set(request_id, list); }
          } catch (error) { throw new PersistenceError(`invalid trace at event ${this.positions.length + 1}: ${errorText(error)}`); }
          this.offset += length; start = end + 1;
        }
        carry = Buffer.from(bytes.subarray(start));
      }
      // A trailing partial record belongs to the next refresh, once its newline is durable.
    } finally { closeSync(fd); }
  }
  page(after = 0, limit = 200) {
    this.refresh(); const events = this.summaries.slice(after, after + limit);
    return { events, total: this.positions.length, next: events.at(-1)?.seq ?? after };
  }
  record(seq: number): Event {
    this.refresh(); const pair = this.positions[seq - 1]; if (!pair) throw new Error('event not found');
    const [position, length] = pair, fd = openSync(this.path, 'r'), bytes = Buffer.alloc(length); let offset = 0;
    try { while (offset < length) { const n = readSync(fd, bytes, offset, length - offset, position + offset); if (!n) throw new PersistenceError('trace changed while reading'); offset += n; } }
    finally { closeSync(fd); }
    return JSON.parse(bytes.toString('utf8')) as Event;
  }
  request(id: string) {
    this.refresh(); const seqs = this.requests.get(id); if (!seqs) throw new Error('request not found');
    const records = seqs.map((seq) => this.record(seq));
    const wire = Buffer.concat(records.filter((r) => r.type === 'api_response_chunk').map((r) => Buffer.from(String(r.data.body_base64), 'base64')));
    const headers = records.find((r) => r.type === 'api_response_start')?.data.headers as [string, string][] | undefined;
    const encoding = headers?.find(([k]) => k.toLowerCase() === 'content-encoding')?.[1].toLowerCase() || '';
    let decoded = wire, decode_error: string | null = null;
    try {
      if (encoding === 'gzip') decoded = gunzipSync(wire);
      else if (encoding === 'deflate') decoded = inflateSync(wire);
      else if (encoding === 'br') decoded = brotliDecompressSync(wire);
      else if (!['', 'identity'].includes(encoding)) throw new Error(`unsupported content encoding: ${encoding}`);
    } catch (error) { decode_error = errorText(error); }
    return { request_id: id, records, response_body_base64: wire.toString('base64'), response_text: decode_error ? null : decoded.toString('utf8'), decode_error };
  }
}
