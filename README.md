English | [简体中文](README.zh-CN.md)

# pi-snap

Compact, read-only execution snapshots for humans and agents using official [Pi](https://github.com/earendil-works/pi). Find failures, inspect tool calls, and expand individual records without feeding an entire JSONL log into an agent's context. No running Pi instance or model requests required.

## Install and use

Requires Python 3.10+. npm installation also requires Node.js 22.19+ and does **not** install Python:

```bash
npm install -g @arkkwang/pi-snap
pi-snap --help
pi-snap                              # Recent sessions
pi-snap <session-id>                 # Compact current-branch snapshot
pi-snap <session-id> --all            # Include discarded branches
pi-snap <session-id> --tail 20        # Last 20 records
pi-snap <session-id> <entry-id>       # Full record content
pi-snap <session-id> <entry-id> --json
pi-snap --session-dir /path/to/sessions
pi-snap /path/to/session.jsonl
```

Make `python3` available on PATH, or set `PI_SNAP_PYTHON` to your Python 3 executable path. The npm entry point does not require Bash. Source use and regression tests require Bash (Git Bash on Windows, Bash on macOS/Linux):

```bash
bash pi-snap --help
# From any directory:
bash /absolute/path/to/pi-snap/pi-snap <session-id>
```

The default session root is `~/.pi/agent/sessions`. Use `--session-dir` for custom Pi data locations. Select a session by a header ID prefix, filename prefix, or JSONL path; ambiguous prefixes produce a diagnostic.

Snapshots shorten user/assistant text, tool arguments, and results, and omit thinking. Full-record views can reveal the original content. By default, only the branch ending at the last record is shown; `--all` marks other branches with `~`.

## Investigation workflow for humans and agents

1. List recent sessions and choose an ID, or provide a log file directly.
2. Inspect a compact snapshot (`--tail 20` for a shorter view) for tool errors, abnormal termination, or missing results.
3. Expand the relevant entry ID. Add `--json` when you need the original record structure.

Use snapshots to narrow the search and full records to verify evidence. Truncation and missing results prove neither success nor ongoing execution. Unsaved streaming progress cannot be recovered. `--json` currently applies to a single entry, not the entire session.

## Limits and privacy

- This is a log inspection tool, not a backup, recovery, or live-state service.
- Opening a session reads the entire file; `--tail` limits display, not reading.
- Invalid JSONL lines are skipped with a warning, including partially written trailing lines. The tool does not repair files.
- Logs and output can contain prompts, paths, credentials, and tool results. Read-only does not mean redacted; do not publish private logs.
- Targets official Pi session formats. Validated locally on Windows Git Bash; macOS/Linux have not yet been locally tested.

## Tests

```bash
npm ci
npm test
# Python/Bash regression only:
bash test/snap.sh
```

Tests use temporary synthetic sessions, covering branches, truncation, errors, ambiguous prefixes, duplicate IDs, paths with spaces, large inputs, and the npm launcher. No private sessions or model calls are used.

## Related projects

- [pi-cron](https://github.com/arkkwang/pi-cron): schedule agent tasks.
- [pi-subagent](https://github.com/arkkwang/pi-subagent): delegate, observe, and continue sub-agent tasks.

## License

[MIT](LICENSE)
