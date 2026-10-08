#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// npm creates native command shims; Python remains an explicit prerequisite.
const python = process.env.PI_SNAP_PYTHON || 'python3';
const result = spawnSync(python, [fileURLToPath(new URL('./snap.py', import.meta.url)), ...process.argv.slice(2)], {
  stdio: 'inherit',
});
if (result.error) {
  console.error(`pi-snap: cannot run ${python}: ${result.error.message}. Install Python 3.10+ or set PI_SNAP_PYTHON to its executable path.`);
}
process.exitCode = result.status ?? 1;
