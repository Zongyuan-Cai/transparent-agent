import http from 'node:http';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { createReadStream, readFileSync, statSync } from 'node:fs';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { safeUrl } from '../provider.js';
import { errorText } from '../types.js';
import { SessionManager, WebError } from './worker.js';

const assets: Record<string, [string, string]> = { '/': ['index.html', 'text/html'], '/app.js': ['app.js', 'text/javascript'], '/styles.css': ['styles.css', 'text/css'] };
export class WebServer {
  server: http.Server; token: string; port = 0; private stopping = false; private streams = new Set<ServerResponse>();
  readonly manager: SessionManager; readonly host: string;
  constructor(manager: SessionManager, host = '127.0.0.1', port = 8765, token = randomBytes(32).toString('base64url')) {
    this.manager = manager; this.host = host;
    if (!['127.0.0.1', 'localhost'].includes(host)) throw new WebError('Web UI only binds to loopback');
    if (!Number.isInteger(port) || port < 0 || port > 65535) throw new WebError('invalid port');
    this.port = port; this.token = token;
    this.server = http.createServer((req, res) => { void this.dispatch(req, res).catch((error) => {
      if (res.headersSent) { res.destroy(); return; }
      req.resume(); this.json(res, { error: errorText(error) }, error instanceof WebError ? error.status : (error as NodeJS.ErrnoException).code === 'ENOENT' ? 404 : 409);
    }); });
    this.server.requestTimeout = 10000;
  }
  get url() { return `http://127.0.0.1:${this.port}`; }
  async listen() {
    await new Promise<void>((resolve, reject) => { this.server.once('error', reject); this.server.listen(this.port, this.host, () => { this.server.off('error', reject); resolve(); }); });
    this.port = (this.server.address() as { port: number }).port; return this;
  }
  async close() {
    if (this.stopping) return; this.stopping = true; for (const stream of this.streams) stream.end();
    const closed = new Promise<void>((resolve) => this.server.close(() => resolve()));
    await this.manager.close(); this.server.closeIdleConnections(); await closed;
  }
  private headers(res: ServerResponse, status: number, mime: string, extra: Record<string, string | number> = {}) {
    res.writeHead(status, { 'Content-Type': mime, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
      'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'", ...extra });
  }
  private json(res: ServerResponse, data: unknown, status = 200) {
    const bytes = Buffer.from(JSON.stringify(data)); this.headers(res, status, 'application/json; charset=utf-8', { 'Content-Length': bytes.length }); res.end(bytes);
  }
  private boundary(req: IncomingMessage, write: boolean) {
    const hosts = [`127.0.0.1:${this.port}`, `localhost:${this.port}`];
    if (!hosts.includes(req.headers.host || '')) throw new WebError('invalid Host', 403);
    if (req.headers.origin && !hosts.map((host) => `http://${host}`).includes(req.headers.origin)) throw new WebError('cross-origin access denied', 403);
    if (write) {
      const supplied = Buffer.from(String(req.headers['x-transparent-token'] || '')), expected = Buffer.from(this.token);
      if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) throw new WebError('missing or invalid browser token', 403);
      if (req.headers['content-type']?.split(';')[0].trim() !== 'application/json') throw new WebError('write requests require application/json', 415);
    }
  }
  private async body(req: IncomingMessage): Promise<Record<string, unknown>> {
    if (req.headers['transfer-encoding']) throw new WebError('chunked request bodies are unsupported');
    const length = Number(req.headers['content-length'] || 0); if (!Number.isInteger(length) || length < 0) throw new WebError('invalid Content-Length');
    if (length > 1024 * 1024) throw new WebError('request body exceeds 1 MiB', 413);
    req.setTimeout(10000, () => req.destroy()); const chunks: Buffer[] = []; let size = 0;
    for await (const chunk of req) { size += chunk.length; if (size > 1024 * 1024) throw new WebError('request body exceeds 1 MiB', 413); chunks.push(chunk as Buffer); }
    try {
      const result: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error(); return result as Record<string, unknown>;
    } catch { throw new WebError('request body must be a JSON object'); }
  }
  private integer(query: URLSearchParams, key: string, fallback: number, min: number, max: number) {
    const raw = query.get(key), n = raw === null ? fallback : /^\d+$/.test(raw) ? Number(raw) : NaN;
    if (!Number.isSafeInteger(n) || n < min || n > max) throw new WebError(`invalid ${key}`); return n;
  }
  private async dispatch(req: IncomingMessage, res: ServerResponse) {
    const write = req.method === 'POST'; if (!['GET', 'POST'].includes(req.method || '')) throw new WebError('method not allowed', 405);
    this.boundary(req, write); const url = new URL(req.url!, this.url); let path;
    try { path = decodeURIComponent(url.pathname); } catch { throw new WebError('invalid URL'); }
    const manager = this.manager;
    if (!write && assets[path]) {
      const [name, mime] = assets[path], bytes = readFileSync(new URL(`./static/${name}`, import.meta.url));
      this.headers(res, 200, `${mime}; charset=utf-8`, { 'Content-Length': bytes.length }); res.end(bytes); return;
    }
    if (!write && path === '/api/config') { const c = manager.config; this.json(res, { workspace: c.workspace, model: c.model, base_url: safeUrl(c.base_url || 'https://api.openai.com/v1'), configured: !!c.api_key, token: this.token, max_steps: c.max_steps }); return; }
    if (path === '/api/sessions') {
      if (!write) this.json(res, { sessions: manager.list() });
      else {
        const body = await this.body(req);
        for (const field of ['workspace', 'model']) if (field in body && (typeof body[field] !== 'string' || !body[field].trim())) throw new WebError(`${field} must be a non-empty string`);
        this.json(res, await manager.create(body.workspace as string | undefined, body.model as string | undefined), 201);
      }
      return;
    }
    const parts = path.split('/').filter(Boolean); if (parts[0] !== 'api' || parts[1] !== 'sessions' || parts.length < 3) throw new WebError('not found', 404);
    const id = parts[2], tail = parts.slice(3).join('/'); manager.path(id);
    if (write) {
      const body = await this.body(req);
      if (tail === 'runs') {
        if (typeof body.text !== 'string' || !body.text.trim()) throw new WebError('text must be a non-empty string');
        await manager.send(id, body.text); this.json(res, manager.status(id), 202);
      } else if (tail === 'cancel') { manager.cancel(id); this.json(res, manager.status(id), 202); }
      else if (tail === 'clear') { await manager.clear(id); this.json(res, manager.detail(id)); }
      else throw new WebError('not found', 404); return;
    }
    if (!tail) this.json(res, manager.detail(id));
    else if (tail === 'events') this.json(res, manager.trace(id).page(this.integer(url.searchParams, 'after', 0, 0, Number.MAX_SAFE_INTEGER), this.integer(url.searchParams, 'limit', 200, 1, 500)));
    else if (tail === 'stream') {
      const cursor = this.integer(url.searchParams, 'after', 0, 0, Number.MAX_SAFE_INTEGER), last = req.headers['last-event-id'];
      if (last && !/^\d+$/.test(String(last))) throw new WebError('invalid Last-Event-ID');
      this.stream(req, res, id, Math.max(cursor, Number(last || 0)));
    } else if (/^events\/\d+$/.test(tail)) {
      try { this.json(res, manager.trace(id).record(Number(parts[4]))); } catch (error) { if (errorText(error) === 'event not found') throw new WebError('event not found', 404); throw error; }
    } else if (/^requests\/[^/]+$/.test(tail)) {
      try { this.json(res, manager.trace(id).request(parts[4])); } catch (error) { if (errorText(error) === 'request not found') throw new WebError('request not found', 404); throw error; }
    } else if (tail === 'trace') {
      const path = manager.trace(id).path, size = statSync(path).size;
      this.headers(res, 200, 'application/x-ndjson', { 'Content-Length': size, 'Content-Disposition': `attachment; filename="${id}-trace.jsonl"` });
      if (!size) { res.end(); return; } const source = createReadStream(path, { start: 0, end: size - 1 }); source.on('error', () => res.destroy()); res.on('close', () => source.destroy()); source.pipe(res);
    } else throw new WebError('not found', 404);
  }
  private stream(_req: IncomingMessage, res: ServerResponse, id: string, after: number) {
    const view = this.manager.trace(id); view.refresh(); this.headers(res, 200, 'text/event-stream; charset=utf-8', { Connection: 'keep-alive' }); res.write('retry: 1000\n\n');
    this.streams.add(res); let status = '', heartbeat = Date.now();
    const tick = () => {
      if (res.destroyed || res.writableLength > 1024 * 1024) return;
      try {
        const page = view.page(after, 50);
        for (const event of page.events) { const more = res.write(`id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`); after = event.seq; if (!more) break; }
        const current = this.manager.status(id), text = JSON.stringify({ status: current.status, error: current.error, stop_reason: current.result?.stop_reason || null });
        if (status !== text) { res.write(`event: status\ndata: ${text}\n\n`); status = text; }
        if (Date.now() - heartbeat > 10000) { res.write(': heartbeat\n\n'); heartbeat = Date.now(); }
      } catch (error) { res.end(`event: trace_error\ndata: ${JSON.stringify({ error: errorText(error) })}\n\n`); }
    };
    const timer = setInterval(tick, 200); res.on('close', () => { clearInterval(timer); this.streams.delete(res); }); tick();
  }
}
