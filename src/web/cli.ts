import { argumentsFor, help, version } from '../arguments.js';
import { SessionManager } from './worker.js';
import { WebServer } from './server.js';
import { errorText } from '../types.js';

export async function main(args = process.argv.slice(2)): Promise<number> {
  let server: WebServer | undefined;
  try {
    const { values, config, options } = argumentsFor(true, args);
    if (values.help) { process.stdout.write(help); return 0; } if (values.version) { process.stdout.write(version + '\n'); return 0; }
    if (options.resume || values.print || values.inspect) throw new Error('Web server uses its session API; --resume, --print and --inspect are terminal options');
    server = await new WebServer(new SessionManager(config, options), typeof values.host === 'string' ? values.host : undefined, Number(values.port || 8765), typeof values.token === 'string' ? values.token : undefined).listen();
    process.stdout.write(`Transparent Agent Web ${version}\n${server.url}\n${config.workspace} · ${config.model}\n`);
    await new Promise<void>((resolve) => {
      const stop = () => { process.off('SIGINT', stop); process.off('SIGTERM', stop); resolve(); };
      process.on('SIGINT', stop); process.on('SIGTERM', stop);
    }); return 0;
  } catch (error) { process.stderr.write(`transparent-agent-web: ${errorText(error)}\n`); return 2; }
  finally { await server?.close(); }
}
