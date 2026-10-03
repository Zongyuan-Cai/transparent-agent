import { parentPort, workerData } from 'node:worker_threads';
import { readdirSync, realpathSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { minimatch } from 'minimatch';
import { inside } from './base.js';
import { text } from './files.js';

const { root, workspace, pattern, glob, ignore_case } = workerData as { root: string; workspace: string; pattern: string; glob?: string; ignore_case?: boolean };
const skip = new Set(['.git', '.hg', '.svn', 'node_modules', '.venv', 'venv', '__pycache__', 'dist', 'build', '.pytest_cache', '.idea', '.vscode']);
const matches: string[] = [], deadline = Date.now() + 30000;
const regex = new RegExp(pattern, ignore_case ? 'i' : '');
function visit(path: string): void {
  if (matches.length >= 200 || Date.now() > deadline) return;
  try {
    const resolved = realpathSync(path); if (!inside(workspace, resolved)) return;
    const info = statSync(path);
    if (info.isDirectory()) {
      for (const entry of readdirSync(path, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        if (!skip.has(entry.name) && !entry.isSymbolicLink()) visit(join(path, entry.name));
      }
    } else if (info.isFile() && info.size <= 2_000_000 && (!glob || minimatch(relative(root, path) || path.split('/').at(-1)!, glob, { matchBase: true, dot: true }))) {
      for (const [i, line] of text(readFileSync(path)).split(/\r?\n/).entries()) {
        if (regex.test(line)) matches.push(`${relative(workspace, path)}:${i + 1}:${line.slice(0, 300)}`);
        if (matches.length >= 200) break;
      }
    }
  } catch { /* inaccessible and binary files are omitted */ }
}
visit(root);
parentPort!.postMessage({ matches, limited: matches.length >= 200 || Date.now() > deadline });
