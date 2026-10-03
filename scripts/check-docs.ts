import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
function markdown(path: string): string[] { return readdirSync(path, { withFileTypes: true }).flatMap((e) => e.isDirectory() ? markdown(join(path, e.name)) : e.name.endsWith('.md') ? [join(path, e.name)] : []); }
const files = [join(root, 'README.md'), ...markdown(join(root, 'docs'))], missing: string[] = [];
for (const file of files) for (const match of readFileSync(file, 'utf8').matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
  const target = match[1].trim().replace(/^<|>$/g, '').split('#')[0];
  if (!target || /^[a-z][a-z0-9+.-]*:/i.test(target)) continue;
  if (!existsSync(resolve(dirname(file), decodeURIComponent(target)))) missing.push(`${file}: ${target}`);
}
if (missing.length) { process.stderr.write(missing.join('\n') + '\n'); process.exitCode = 1; }
else process.stdout.write(`Validated local links in ${files.length} Markdown documents.\n`);
