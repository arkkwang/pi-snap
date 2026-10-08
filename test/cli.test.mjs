import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
const entry = fileURLToPath(new URL('../cli.mjs', import.meta.url));
const run = (args, env = process.env) => spawnSync(process.execPath, [entry, ...args], { encoding: 'utf8', env });

test('npm entry forwards help and parser exit codes', () => {
  const help = run(['--help']);
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /--session-dir/);
  assert.equal(run(['--unknown']).status, 2);
});
test('npm entry preserves paths with spaces and raw JSON output', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'pi snap cli '));
  try {
    const file = path.join(dir, 'session file.jsonl');
    const entry = { type: 'message', id: 'u1', parentId: null, message: { role: 'user', content: 'hello' } };
    writeFileSync(file, JSON.stringify({ type: 'session', id: 'fixture', version: 3 }) + '\n' + JSON.stringify(entry) + '\n');
    const result = run([file, 'u1', '--json']);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), entry);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
test('missing Python gives an actionable nonzero error', () => {
  const result = run(['--help'], { ...process.env, PI_SNAP_PYTHON: 'pi-snap-missing-python-executable' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Install Python 3.10\+ or set PI_SNAP_PYTHON/);
});
