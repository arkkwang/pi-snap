English | [简体中文](README.zh-CN.md)

# pi-snap

Session trajectories, execution summaries, and session fork exports for humans and agents using official [Pi](https://github.com/earendil-works/pi). Understand requests and discussion first, then inspect tool calls, expand individual records, and export an independent session at a selected entry. Source sessions are never modified. No running Pi instance or model requests required.

## Install and use

Requires Python 3.10+. npm installation also requires Node.js 22.19+ and does **not** install Python:

```bash
npm install -g @arkkwang/pi-snap
pi-snap --help
pi-snap                              # Recent sessions
pi-snap <session-id>                 # Full dialogue, tool parameters and status
pi-snap <session-id> --trace          # Compact execution view
pi-snap <session-id> --all            # Include discarded branches
pi-snap <session-id> --tail 20        # Last 20 visible records in this view
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

The default view is the session trajectory: full user/assistant text and line breaks, tool requests with key arguments capped at 100 characters (`TRAJECTORY_ARGS_MAX` in `snap.py`, overriding larger per-tool limits), and system/environment summaries. Tool results surface only on failure or unknown status, as a one-line status (`ERR`/`unknown`); successful executions are omitted entirely. Assistant responses that failed before producing content, and `context_edit` markers that remove records from the model context, are host-side recovery bookkeeping and stay out of the trajectory; `--trace` shows these execution events, while compaction and branch-summary boundaries remain visible. Thinking, image payloads and **all tool-result bodies** are omitted; user images keep an `[image]` placeholder. Unknown-tool arguments prioritize model/provider/cwd/task and omit common payload fields; this is summarization, not secret redaction. A saved request without a result does not prove execution or cancellation. Calls inside Code Mode are not inferred from scripts or output.

`compaction`, `branch_summary` and `context_edit` remain at their original positions without summary/replacement bodies. Actual tool messages saved inside `capability-prefetch` custom-message `details.prefetch.rounds[].messages` keep the original source ID, marked `[recommend]`; suggestions are never counted as execution.

Preparation tools use their embedded execution timestamps (including epoch milliseconds), with a separate `[落盘 …]` persistence-time annotation. A batch with complete, consistent timing may move backwards along its own ancestor chain, within the same user/context interval, so delayed persistence does not make preparation appear to follow main execution. Ordinary entries are never globally time-sorted. Missing execution times are labelled unknown; conflicting or insufficient timing keeps the original position. `--all` preserves persistence order. Raw-ID ranges are selected before reordering in-window preparation, filtering and `--tail`; out-of-window records are not pulled in.

`--trace` also shows `[discovery]` summaries of `capability-prefetch-diagnostic` entries: input settings, review gate reasons/counts, context delivery, outcomes and child session IDs. `configuredReview` and path-local `effectiveReview` are separate; missing fields in older logs do not establish the global switch. Diagnostics stay out of the default trajectory. Inspect full fields with the original ID and `--json`.

`--trace` explicitly enables the existing compact execution summaries, including bounded result previews, shorter dialogue, failed responses and context-edit markers. Both views share one renderer; use `SESSION ENTRY` (or `--json`) for the original record. Both default to the branch ending at the file's last record; `--all` includes other branches with `~` marks. There is no global character cap or stdin mode: callers may use `--tail` and crop the returned text themselves without rebuilding trajectory semantics.

**Integration requirement:** install a release with this new default before enabling callers that depend on result exclusion (bodies always; successful results entirely); 0.3.1 and earlier use the old dialogue-only default. A JSONL file must already reflect the active branch at the read boundary; pi-snap cannot see an in-memory-only branch cursor.

## Expand a discussion segment

```bash
pi-snap SESSION
pi-snap SESSION --trace --since ID1 --before ID2
pi-snap SESSION ENTRY
pi-snap SESSION ENTRY --json
```

- Views, ranges and details use original entry IDs, including the parent custom-entry ID for embedded prefetch messages. Unique prefixes work; missing, ambiguous or duplicate IDs fail explicitly.
- `--since ID1` includes its boundary; `--before ID2` excludes it. Either boundary is optional. Ordering follows the record chain, not timestamps or lexical ID order.
- Bounds may refer to tool records or dialogue records. Off-branch or reversed bounds fail; identical bounds produce an explicitly empty range.
- Ranges cannot be combined with `--all`, preventing silent cross-branch stitching.
- Selection order: branch → raw-ID range → in-window preparation projection → view filter → `--tail N`. N must be positive. There is no implicit tail limit, including for ranges. Tail omission reports the number of earlier visible records.
- Mixed assistant records keep their text and tool calls together. A record with multiple calls counts once toward `--tail`, not once per output line; each expanded prefetch message counts separately.
- Trace inventories and call/result pairing use the whole selected branch: a result outside the displayed window is not a missing result.

### Execution-view formatting

`--trace` display rules live at the top of `snap.py`: `USER_MAX=800`, `ASST_MAX=600`, default tool input `ARGS_MAX=120`, and result preview `PEEK_MAX=100`. `TOOL_SUMMARIES` configures input templates, limits and result summaries by tool name, without tool-specific rendering branches. For example:

```python
"bash": {"max_chars": 600, "template": "{command}"},
"tool_search": {"max_chars": 400},  # No template: keep the entire arguments JSON
"example": {
    "template": "{path} · {items:count}",
    "result": {"template": "{status} {lines} lines {size}", "preview_chars": 0},
},
```

- Input templates select arguments by name; `:size` formats content character counts and `:count` counts items. Missing fields are empty. Optional segments require at least one non-`None` value in `when_any`; `defaults` fills missing or `None` values, while zero remains valid. See the Read configuration.
- Unconfigured tools or entries without an input template use the entire arguments JSON on one line. Non-object arguments retain their content rather than becoming an empty object. Input limits apply with an omitted-character count. `truncate="middle"` preserves the first third and last two thirds for Read/Write/Edit/ls paths and suffixes, without parsing paths. Extremely long filenames may still be shortened, retaining their ending. The default preserves the beginning.
- Result templates support `{status}`, `{size}`, `{lines}`, and `{duration}` (including a leading space when present). The default shows status, size and duration. `preview_chars` controls successful-result previews; `error_preview_chars` independently controls error previews. Both default to 100; zero disables that preview. Read suppresses only successful-result previews. Image markers remain visible.

Limits count characters after whitespace folding; original records and full views are unchanged.

System summaries stay on one line: `cwd=… · Skills=13 · MCP=5 · Tools=6 · Instructions=1 · Prompt=8.2Kc`. Counts refer to available skills, MCP servers, tools, and project instruction blocks; absent sections are omitted. Tool inventories follow each parentId branch, with subsequent changes shown as `Tools=8(+2)`, including when using `--tail`. Prompt counts characters in this record's sections and content (1Kc = 1000 characters), not tokens, excluding tool definitions. Incremental records do not represent the full context size.

## Investigation workflow for humans and agents

1. List recent sessions and choose an ID, or provide a log file directly.
2. Read the default dialogue view (`--tail 20` for a shorter view) to understand requests, corrections, and conclusions.
3. Use the same IDs with `--trace --since ID1 --before ID2` to examine the relevant execution segment.
4. Expand an individual entry ID; add `--json` for the original record structure.

Assistant claims and tool requests are not success evidence. Use execution snapshots to narrow the search and full records to verify evidence. Truncation and missing results prove neither success nor ongoing execution. Unsaved streaming progress cannot be recovered. `--json` currently applies to a single entry, not the entire session.

## Fork a session

```bash
pi-snap <session-id> <entry-id> --fork ./retry.jsonl
pi --session ./retry.jsonl
```

Select an entry ID from a snapshot (unique prefixes also work). The export follows `parentId` from the root **through the selected entry, inclusive**, even on a discarded branch. Siblings and later entries are excluded. To retry an assistant response, select the preceding user message or the last result of its preceding tool batch, not the response itself.

The new header gets a fresh session ID, timestamp, and `parentSession` pointing to the source file. Other header fields and all selected records are preserved, including thinking, images, system records, model settings, compactions, and labels. No summaries are recomputed and no later information is imported. Change models/prompts and continue through your resume interface. Export does not restore files, processes, or external services, nor guarantee that a resume interface treats logged system records as its active system prompt.

- Requires a v3 session, a non-existing destination file, and an existing parent directory. Success prints the absolute output path to stdout.
- Cannot be combined with `--all`, `--tail`, `--trace`, `--since`, `--before`, or `--json`.
- Malformed lines, duplicate IDs, broken/cyclic ancestry, invalid compaction references, and unpaired tool results fail export rather than silently losing or inventing records. Select a point before a tool batch or after all its results.
- Only persisted records are exported. External attachment references remain the caller's responsibility.

## Limits and privacy

- This is a log inspection and session fork tool, not an environment backup, rollback, or live-state service.
- Opening a session reads the entire file; `--tail` limits display, not reading.
- Inspection skips invalid JSONL lines with a warning; fork export rejects them. The tool does not repair files.
- Logs and output can contain prompts, paths, credentials, and tool results. Read-only does not mean redacted; do not publish private logs.
- Targets official Pi session formats. Validated locally on Windows Git Bash; macOS/Linux have not yet been locally tested.

## Tests

```bash
npm ci
npm test
# Python/Bash regression only:
bash test/snap.sh
```

Tests use temporary synthetic sessions, covering branches, truncation, errors, ambiguous prefixes, duplicate IDs, paths with spaces, large inputs, fork integrity, overwrite protection, and the npm launcher. No private sessions or model calls are used.

Optional SDK contract test: set `PI_SNAP_SESSION_MANAGER` to your installed Pi's `dist/core/session-manager.js`, then run `npm test`. The real `SessionManager` opens exported sessions and verifies restored context equivalence at the selected entry. Without this variable, only CLI checks run; Pi is not installed automatically. Run `python3 test/benchmark.py` for a repeatable 20,000-entry benchmark (median of five warm runs).

## Related projects

- [pi-cron](https://github.com/arkkwang/pi-cron): schedule agent tasks.
- [pi-subagent](https://github.com/arkkwang/pi-subagent): delegate, observe, and continue sub-agent tasks.

## License

[MIT](LICENSE)
