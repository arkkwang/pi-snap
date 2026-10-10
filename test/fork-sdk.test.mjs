import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const cli = fileURLToPath(new URL('../cli.mjs', import.meta.url));
// Optional local SDK contract test, without adding Pi as a runtime dependency.
// PI_SNAP_SESSION_MANAGER points to Pi's dist/core/session-manager.js.
const sdkPath = process.env.PI_SNAP_SESSION_MANAGER;
test('npm fork export; optional Pi SDK context equivalence', async (t) => {
  const dir = mkdtempSync(path.join(tmpdir(), 'pi snap sdk '));
  try {
    const source = path.join(dir, 'source.jsonl');
    const stamp = '2026-01-01T00:00:00.000Z';
    const header = { type: 'session', version: 3, id: 'source', timestamp: stamp, cwd: dir };
    const msg = (id, parentId, message) => ({ type: 'message', id, parentId, timestamp: stamp, message });
    const entries = [
      { type: 'model_change', id: 'model', parentId: null, timestamp: stamp, provider: 'test', modelId: 'test-model' },
      { type: 'thinking_level_change', id: 'thinking', parentId: 'model', timestamp: stamp, thinkingLevel: 'medium' },
      msg('system', 'thinking', { role: 'system', content: 'Original instructions', sections: { preamble: 'Original instructions' }, toolsAdded: [] }),
      msg('user', 'system', { role: 'user', content: [{ type: 'text', text: 'question' }], timestamp: 1 }),
      msg('call', 'user', { role: 'assistant', content: [{ type: 'toolCall', id: 'tc', name: 'read', arguments: { path: 'x' } }], stopReason: 'toolUse', timestamp: 2 }),
      msg('result', 'call', { role: 'toolResult', toolCallId: 'tc', toolName: 'read', content: [{ type: 'text', text: 'error' }], isError: true, timestamp: 3 }),
      msg('discarded', 'result', { role: 'user', content: 'discarded branch', timestamp: 4 }),
      { type: 'label', id: 'label', parentId: 'result', timestamp: stamp, targetId: 'user', label: 'checkpoint' },
      { type: 'compaction', id: 'compact', parentId: 'label', timestamp: stamp, summary: 'past summary', firstKeptEntryId: 'user', tokensBefore: 100 },
      msg('future', 'compact', { role: 'user', content: 'future must not leak', timestamp: 5 }),
    ];
    const raw = [header, ...entries].map(e => JSON.stringify(e)).join('\n') + '\n';
    writeFileSync(source, raw);
    const sdk = sdkPath ? await import(pathToFileURL(path.resolve(sdkPath)).href) : null;
    if (!sdk) t.diagnostic('Pi SDK comparison not run; set PI_SNAP_SESSION_MANAGER to enable.');
    for (const leaf of ['user', 'result', 'discarded', 'compact']) {
      const destination = path.join(dir, `${leaf} fork.jsonl`);
      const result = spawnSync(process.execPath, [cli, source, leaf, '--fork', destination], { encoding: 'utf8' });
      assert.equal(result.status, 0, result.stderr);
      assert.equal(result.stdout.trim(), destination);
      const exported = readFileSync(destination, 'utf8').trim().split('\n').map(JSON.parse);
      assert.notEqual(exported[0].id, header.id);
      assert.equal(exported.at(-1).id, leaf);
      assert.ok(!exported.some(e => e.id === 'future'));
      if (sdk) {
        const session = sdk.SessionManager.open(destination, dir);
        assert.equal(session.getLeafId(), leaf);
        assert.deepEqual(session.buildSessionContext(), sdk.buildSessionContext(entries, leaf));
        assert.deepEqual(session.getEntries(), exported.slice(1));
      }
    }
    assert.equal(readFileSync(source, 'utf8'), raw);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
