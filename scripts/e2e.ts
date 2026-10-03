import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
import { Runtime, configFromEnv, loadDotenv } from '../dist/index.js';

loadDotenv();
const root = mkdtempSync(join(tmpdir(), 'transparent-live-')), runtime = await Runtime.create(configFromEnv({ workspace: root, max_steps: 8 }), { state_dir: join(root, '.state') });
try {
  const result = await runtime.agent.send('Use write to create verified.txt with exactly transparent-agent verified, then use read to verify it. Report the result.');
  assert.equal(result.stop_reason, 'final'); assert.equal(readFileSync(join(root, 'verified.txt'), 'utf8').trim(), 'transparent-agent verified');
  process.stdout.write('Live API validation passed in a temporary workspace.\n');
} finally { await runtime.close(); rmSync(root, { recursive: true, force: true }); }
