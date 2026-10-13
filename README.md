English | [简体中文](README.zh-CN.md)

# pi-snap

**Read, filter, and display Pi session records** for humans and agents. Scan conversations and execution paths as compact single lines, open full evidence by record number, or export an independent session through a selected record.

The original JSON is preserved. New types, content blocks, and fields have a generic fallback instead of requiring individual adapters. Filters select content; one renderer presents it; presets are just option bundles. No model calls or changes to source sessions.

```text
[   2] 12:05:23 [user] Check this file
[ 128] 12:05:25 [→ read] path=/project/file.py, offset=10
[ 132] 12:05:26 [assistant] Check complete.
```

## Install and use

Requires Python 3.10+. The npm launcher also requires Node.js 22.19+ and does not install Python.

```bash
npm install -g @arkkwang/pi-snap
pi-snap                              # List recent sessions
pi-snap SESSION                      # Single-line records on the current branch
pi-snap SESSION --preset trajectory  # Dialogue, call requests, recommendations and warnings
pi-snap SESSION --only user,assistant --exclude thinking,image
pi-snap SESSION --preset tools --max-chars 500 --tail 20
pi-snap SESSION 128                  # Complete individual record
pi-snap SESSION 128 --json           # Original JSON
```

`SESSION` accepts a session ID prefix, filename prefix, or JSONL path. Sessions default to `~/.pi/agent/sessions`; override with `--session-dir PATH`. Run source with `python3 snap.py` or `bash pi-snap`. Set `PI_SNAP_PYTHON` to choose Python for the npm launcher.

## Display and numbering

- Valid source records are numbered from 1, including the session header, before branch/range/filter selection. Filtering and `--tail` do not renumber them. Appending records preserves existing numbers; editing old records can change them.
- Numbers are right-aligned and space-padded to at least four digits. The whole column grows beyond 9,999 records, based on the complete file. Original IDs remain in details.
- Text blocks and calls produce separate lines with the same source number. Filtered records disappear without omission notices.
- Text is folded into one line, limited to 300 body characters by default. Set `--max-chars N`, or `0` for unlimited. Truncation is marked at the line's end; navigation columns are not truncated.
- Established metadata such as usage and signatures stays in details. New types, blocks, and unrecognized fields use compact `key=value` output. There are no per-tool summary templates.
- Times use local `HH:mm:ss`, without a time label or milliseconds. The date appears in the session header and on cross-day records. Sorting and time filtering retain full precision.

## Filters and presets

`--only TYPES` includes matching objects and their subtrees. `--exclude TYPES` removes matching subtrees and takes precedence. Options accept comma-separated values and can be repeated. Matching is recursive, exact, and case-sensitive against string-valued `type`, `role`, and `customType`. Qualify selectors such as `--only role:assistant` or `--only type:new-event`; values containing a colon require qualification.

A mixed message can exclude thinking while retaining text and calls. Selecting only a descendant preserves its source and ancestor path without unrelated siblings. Structural matching is neither execution evidence nor secret redaction.

| Preset | Include | Exclude |
| --- | --- | --- |
| `all` (default) | everything | none |
| `conversation` | `role:user,role:assistant` | `type:thinking,type:toolCall` |
| `tools` | `type:toolCall,role:toolResult` | none |
| `trajectory` | `role:user,role:assistant,type:toolCall,customType:capability-prefetch,customType:capability-trajectory-warning` | `type:thinking,role:toolResult,type:image` |

Explicit `--only` replaces the preset's inclusion list, `--exclude` adds exclusions, and `--max-chars` overrides length. All presets use the same single-line renderer. New types display by default; an inclusion list determines whether they are selected.

## Navigation and timing

```bash
pi-snap SESSION --since 128 --before 160
pi-snap SESSION --since-time 2026-10-13T08:00:00+08:00 --before-time 2026-10-13T08:05:00+08:00
pi-snap SESSION --all
```

- Details, ranges, and fork exports accept a number or original ID/unique prefix. Integers mean record numbers; use `id:0532` for a numeric original ID.
- Default selection follows `parentId` from the file's last record and includes the session header. `--all` keeps file order and marks other branches with `~`; it cannot be combined with record ranges.
- Ranges include the start and exclude the end. Record bounds are resolved before reordering and filtering; time bounds require a timezone. Invalid, ambiguous, duplicate, off-branch, or reversed bounds fail.
- `--tail N` selects the last N source records after filtering, not output lines. Entry details retain full text and every field, reject filtering/length options, and support raw `--json`.
- Ordinary messages prefer their own timestamp, falling back to the Pi entry timestamp when absent. Preparation uses actual request/result times in `details.prefetch.rounds[].messages`, never substituting outer persistence time. Full precision and persistence time remain in details.
- Delayed preparation moves earlier only with complete, consistent timing and a known ancestral lower bound, without crossing user/system, context, or record-window boundaries. Numbers may therefore locally decrease. `--all` never reorders. A time-window match on any batch event retains its source record; unknown times cannot match and produce a warning, while conflicting times do not establish order.

## Fork a session

```bash
pi-snap SESSION 128 --fork ./retry.jsonl
pi --session ./retry.jsonl
```

Export follows `parentId` through the selected record, inclusive, including on another branch. Requires Pi v3, a non-existing destination, and an existing parent directory. The new header gets a fresh session ID, timestamp, and `parentSession`; other fields are preserved. Files, processes, and external services are not restored.

Export cannot be combined with filters, ranges, length, presets, or `--json`. Malformed lines, invalid/duplicate IDs, broken/cyclic ancestry, invalid compaction references, and unpaired tool results fail. Choose a point before a tool batch or after all its results.

## Notes and tests

- Default type selection includes thinking, tool results, and images. The old `--trace` is removed; use filters and presets.
- The entire file is read before filtering; `--tail` does not reduce reading, and per-line limits are not a total output cap. Inspection warns and skips invalid lines; export rejects them.
- Generic display cannot automatically interpret future branch, timing, or resume protocols. Logs may contain credentials. Unsaved content cannot be recovered, and missing results prove neither execution nor cancellation.

Run `npm test` for synthetic-session regressions and `python3 test/benchmark.py` for a repeatable benchmark. Optional real Pi SDK verification: set `PI_SNAP_SESSION_MANAGER` to an installed Pi's `dist/core/session-manager.js`. Local validation uses Windows Git Bash.

[MIT License](LICENSE)
