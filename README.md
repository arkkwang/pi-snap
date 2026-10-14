English | [简体中文](README.zh-CN.md)

# pi-snap

**Read, filter, and display Pi session records** for humans and agents. Scan conversations and execution paths as compact single lines, open full evidence by record number, or export an independent session through a selected record.

The original JSON is preserved. New types, content blocks, and fields have a generic fallback instead of requiring individual adapters. Filters select content; one renderer presents it; views are just option bundles. No model calls or changes to source sessions.

```text
[   2] 12:05:23 [type:message > role:user > type:text] Check this file
[ 128] 12:05:25 [type:message > role:assistant > type:toolCall] → read path=/project/file.py, offset=10
[ 132] 12:05:26 [type:message > role:assistant > type:text] Check complete.
```

## Install and use

Requires Python 3.10+. The npm launcher also requires Node.js 22.19+ and does not install Python.

```bash
npm install -g @arkkwang/pi-snap
pi-snap                              # List recent sessions
pi-snap --list-views                 # Show both views and their defaults
pi-snap SESSION --view story         # Read the dialogue mainline, full-length bodies
pi-snap SESSION --view trace         # Dialogue, call requests, recommendations
pi-snap SESSION --list-types         # Actual tag paths + copyable selectors
pi-snap SESSION                      # Full compact display (no view)
pi-snap SESSION --only user,assistant --exclude thinking,image
pi-snap SESSION --view trace --max-chars 500 --tail 20
pi-snap SESSION 128                  # Complete individual record
pi-snap SESSION 128 --json           # Original JSON
```

`SESSION` accepts a session ID prefix, filename prefix, or JSONL path. Sessions default to `~/.pi/agent/sessions`; override with `--session-dir PATH`. Run source with `python3 snap.py` or `bash pi-snap`. Set `PI_SNAP_PYTHON` to choose Python for the npm launcher.

## Display and numbering

- Valid source records are numbered from 1, including the session header, before branch/range/filter selection. Filtering and `--tail` do not renumber them. Appending records preserves existing numbers; editing old records can change them.
- Numbers are right-aligned and space-padded to at least four digits. The whole column grows beyond 9,999 records, based on the complete file. Original IDs remain in details.
- Each row shows `[number] time [tag chain] body`. The tag chain is the real source nesting of qualified tags, e.g. `type:message > role:assistant > type:toolCall`; several tags on the same node are all kept (`type:custom_message+customType:capability-prefetch`). Tool names live in the body: `→ read …` starts a call, `← read …` a result. Nested preparation rows keep their host record tags — they never masquerade as top-level assistant messages. Rows without any tags fall back to `record`.
- Text blocks and calls produce separate lines with the same source number. Filtered records disappear without omission notices.
- Text is folded into one line, limited to 300 body characters by default. Set `--max-chars N`, or `0` for unlimited. Truncation is marked at the line's end; navigation columns are not truncated.
- Established metadata such as usage and signatures stays in details. New types, blocks, and unrecognized fields use compact `key=value` output. There are no per-tool summary templates.
- Times use local `HH:mm:ss`, without a time label or milliseconds. The date appears in the session header and on cross-day records. Sorting and time filtering retain full precision.

## Views and filters

`--view story|trace` selects a view; without `--view` everything is displayed. Views are option bundles — the same selectors, filtering, and renderer as everywhere else:

| View | Keeps | Hides | Body length |
| --- | --- | --- | --- |
| `story` | real user and assistant prose, including text written before a call | thinking, tool calls/results, images, capability recommendations (`capability-prefetch`), records left without a body | `0` (unlimited) |
| `trace` | dialogue, call requests, behaviour recommendations and warnings | thinking, tool results, image data | `300` |

- `story` keeps mid-conversation user corrections and generates no summaries. Its empty-shell suppression is part of the view: a message whose every content part was filtered out disappears entirely. Without `--view`, custom `--only`/`--exclude` keep their current behavior — filtered envelopes still display as `content=[]` rows.
- Explicit `--only` replaces the view's inclusion list, `--exclude` appends to its exclusions (exclusion wins), and `--max-chars` overrides its length default.
- `pi-snap --list-views` (no session needed) shows each view's purpose, what it hides, and its selector defaults; views are defined once in `VIEWS`.

`--only TYPES` includes matching objects and their subtrees. `--exclude TYPES` removes matching subtrees and takes precedence. Options accept comma-separated values and can be repeated. Matching is recursive, exact, and case-sensitive against string-valued `type`, `role`, and `customType`. Qualify selectors such as `--only role:assistant` or `--only type:new-event`; values containing a colon require qualification.

A mixed message can exclude thinking while retaining text and calls. Selecting only a descendant preserves its source and ancestor path without unrelated siblings. Structural matching is neither execution evidence nor secret redaction.

## Type discovery

`pi-snap SESSION --list-types` lists the structure paths that actually occur, with a copyable qualified selector per line:

```text
$.message.role                                 role:user
$.message.content[].type                       type:toolCall
$.details.prefetch.rounds[].messages[].role    role:toolResult
```

Array positions collapse to `[]`; identical path+selector pairs are listed once. Discovery runs on the current branch (or `--all`) and supports `--since`/`--before`/`--since-time`/`--before-time`, always before any view or filter selection — types a view hides stay discoverable. It combines with nothing else (views, filters, length, `--tail`, entry, `--json`, `--fork`) and fails with a clear error. Discovery reads structure only: strings are never parsed as nested JSON and prose never becomes an event. Unknown plugin fields are listed without interpretation.

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

Export cannot be combined with views, filters, ranges, length, or `--json`. Malformed lines, invalid/duplicate IDs, broken/cyclic ancestry, invalid compaction references, and unpaired tool results fail. Choose a point before a tool batch or after all its results.

## Notes and tests

- Default type selection includes thinking, tool results, and images. The old `--trace` and `--preset` flags are removed; unknown options fail with argparse's standard error. Use `--view` or explicit filters.
- The entire file is read before filtering; `--tail` does not reduce reading, and per-line limits are not a total output cap. Inspection warns and skips invalid lines; export rejects them.
- Generic display cannot automatically interpret future branch, timing, or resume protocols. Logs may contain credentials. Unsaved content cannot be recovered, and missing results prove neither execution nor cancellation.

Run `npm test` for synthetic-session regressions and `python3 test/benchmark.py` for a repeatable benchmark. Optional real Pi SDK verification: set `PI_SNAP_SESSION_MANAGER` to an installed Pi's `dist/core/session-manager.js`. Local validation uses Windows Git Bash.

[MIT License](LICENSE)
