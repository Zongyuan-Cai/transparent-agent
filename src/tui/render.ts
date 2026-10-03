import type { Event, EventSink } from '../types.js';

export class Renderer implements EventSink {
  private midLine = false;
  private plain: boolean; private color: boolean;
  constructor(plain = false, color = !!process.stdout.isTTY) { this.plain = plain; this.color = color; }
  private style(code: number, text: string) { return this.color ? `\x1b[${code}m${text}\x1b[0m` : text; }
  private line() { if (this.midLine) { process.stdout.write('\n'); this.midLine = false; } }
  emit(event: Event): void {
    const d = event.data, log = (text: string) => (this.plain ? process.stderr : process.stdout).write(text + '\n');
    if (event.type === 'model_text_delta') {
      if (!this.midLine && !this.plain) process.stdout.write(this.style(36, '●') + ' ');
      process.stdout.write(String(d.text)); this.midLine = true;
    } else if (event.type === 'model_response') this.line();
    else if (event.type === 'tool_call_start') {
      this.line(); let text = String(d.arguments);
      try { const a = JSON.parse(text); text = a.command || a.path || a.pattern || a.name || text; } catch { /* Show malformed arguments as received. */ }
      log(`  ${this.style(36, '→')} ${d.name} ${this.style(2, text.replace(/\r?\n/g, ' ⏎ ').slice(0, 110))}`);
    } else if (event.type === 'tool_call_end') {
      this.line(); let lines = String(d.display || d.output || '').split('\n');
      if (lines.length > 20) lines = [...lines.slice(0, 10), `... (${lines.length - 20} more lines)`, ...lines.slice(-10)];
      for (const line of lines.join('\n').slice(0, 4000).split('\n')) log('  ' + this.style(d.is_error ? 31 : 2, line));
    } else if (event.type === 'agent_error') { this.line(); log(this.style(31, `✗ ${d.message}`)); }
    else if (event.type === 'agent_finish') {
      this.line(); const usage = d.usage as Record<string, number>;
      if (d.stop_reason === 'final') log(this.style(2, `✓ done (${d.steps} step${d.steps === 1 ? '' : 's'}${usage?.total_tokens ? `, ${usage.total_tokens} tokens` : ''})`));
      else if (d.stop_reason !== 'error') log(this.style(33, d.stop_reason === 'cancelled' ? '⚠ interrupted' : `⚠ ${d.error}`));
    }
  }
}
