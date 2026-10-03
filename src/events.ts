import { randomUUID } from 'node:crypto';
import type { Data, Event, EventSink } from './types.js';
import { PersistenceError, errorText } from './types.js';

export class EventBus {
  session_id: string; seq: number; run_id: string | null = null; request_id: string | null = null;
  recorder: EventSink; observers: EventSink[];
  constructor(session_id: string, recorder: EventSink, observers: EventSink[] = [], seq = 0) {
    this.session_id = session_id; this.recorder = recorder; this.observers = observers; this.seq = seq;
  }
  beginRun(): void { this.run_id = randomUUID().replaceAll('-', ''); this.request_id = null; }
  endRun(): void { this.run_id = this.request_id = null; }
  publish(type: string, data: object): void {
    let encoded: Data;
    try { encoded = JSON.parse(JSON.stringify(data, (_key, value: unknown) => {
      if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('event contains a non-finite number');
      return value;
    })) as Data; }
    catch (error) { throw new PersistenceError(`cannot serialize event: ${errorText(error)}`); }
    const event: Event = { schema_version: 1, session_id: this.session_id, run_id: this.run_id,
      seq: ++this.seq, type, timestamp: new Date().toISOString(), data: encoded };
    this.recorder.emit(event);
    for (const observer of this.observers) {
      try { observer.emit(structuredClone(event)); }
      catch (error) {
        if (error instanceof PersistenceError) throw error;
        process.stderr.write(`[transparent-agent] observer failed: ${errorText(error)}\n`);
      }
    }
  }
}
