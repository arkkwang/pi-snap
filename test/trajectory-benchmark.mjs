// Startup/output benchmark: node test/trajectory-benchmark.mjs
// Complements benchmark.py's 20,000-record core processing benchmark.
// All launchers point to this checkout, never a stale global installation.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const cli = fileURLToPath(new URL('../cli.mjs', import.meta.url));
const snap = fileURLToPath(new URL('../snap.py', import.meta.url));
const bashEntry = fileURLToPath(new URL('../pi-snap', import.meta.url));
const python = process.env.PI_SNAP_PYTHON || 'python3';
const entries = [];
for (let i = 0; i < 100; i++) {
  entries.push({ type: 'message', message: { role: 'user', content: `任务 ${i} ` + 'request '.repeat(40) } });
  entries.push({ type: 'message', message: { role: 'assistant', content: [
    { type: 'text', text: 'checking '.repeat(40) }, { type: 'thinking', thinking: 'private '.repeat(250) },
    { type: 'toolCall', id: `c${i}`, name: 'bash', arguments: { command: `check --step ${i}` } },
  ] } });
  entries.push({ type: 'message', message: { role: 'toolResult', toolCallId: `c${i}`, toolName: 'bash',
    isError: false, content: [{ type: 'text', text: 'output '.repeat(1500) }] } });
}
const input = entries.map((entry, i) => JSON.stringify({ ...entry, id: `e${i}`, parentId: i ? `e${i - 1}` : null })).join('\n');
const dir = mkdtempSync(path.join(tmpdir(), 'pi-snap-bench '));
try {
  const file = path.join(dir, 'session.jsonl');
  writeFileSync(file, input);
  console.log(JSON.stringify({ platform: process.platform, node: process.version, records: entries.length,
    inputBytes: Buffer.byteLength(input), samples: 15 }));
  const cases = [
    ['python-start', python, ['-c', 'pass']],
    ['python-help', python, [snap, '--help']],
    ['node-help', process.execPath, [cli, '--help']],
    ['bash-help', 'bash', [bashEntry, '--help']],
  ];
  for (const [view, options] of [
    ['all', []],
    ['trajectory', ['--preset', 'trajectory']],
    ['filtered', ['--exclude', 'thinking,image', '--max-chars', '100']],
  ]) {
    const args = [file, ...options, '--tail', '160'];
    cases.push([`python-${view}`, python, [snap, ...args]],
      [`node-${view}`, process.execPath, [cli, ...args]],
      [`bash-${view}`, 'bash', [bashEntry, ...args]]);
  }
  for (const [label, command, argv] of cases) {
    const times = [];
    let chars;
    for (let i = 0; i < 16; i++) {
      const start = performance.now();
      const result = spawnSync(command, argv, { encoding: 'utf8', timeout: 10000, maxBuffer: 8 * 1024 * 1024 });
      const ms = performance.now() - start;
      assert.equal(result.status, 0, result.stderr || result.error?.message);
      chars = result.stdout.length;
      if (label.endsWith('-all')) assert.ok(result.stdout.includes('private '));
      if (label.endsWith('-trajectory')) {
        assert.ok(result.stdout.includes('check --step'));
        assert.ok(!result.stdout.includes('private '));
        assert.ok(!result.stdout.includes('output output'));
      }
      if (label.endsWith('-filtered')) {
        assert.ok(result.stdout.includes('…(+'));
        assert.ok(!result.stdout.includes('private '));
      }
      if (i) times.push(ms);
    }
    times.sort((a, b) => a - b);
    console.log(JSON.stringify({ label, medianMs: +times[7].toFixed(1), p95Ms: +times[14].toFixed(1), outputChars: chars }));
  }
} finally {
  rmSync(dir, { recursive: true, force: true });
}
