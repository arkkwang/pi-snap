#!/usr/bin/env python3
# pi-snap — 通用 Pi 会话记录读取、筛选、展示与分叉导出。
# JSON 结构是展示底座；未知字段不需要注册，视图只是筛选和长度配置。
# 分支按文件最后一条记录的 parentId 链选择，不推断运行时游标或执行状态。
# 已知 Pi 时间语义单独适配；预取的执行时间与落盘时间不混用。
import argparse
import glob
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from uuid import uuid4

# Views are ordinary option bundles. No view-specific rendering paths.
# story hides machinery rows and keeps full-length bodies for reading;
# trace keeps dialogue, call requests and behaviour recommendations.
DEFAULT_MAX_CHARS = 300
VIEWS = {
    "story": {
        "purpose": "通读对话主线：保留用户与 assistant 的真实正文（含调用前说明），正文默认完整不截断",
        "hidden": "隐藏 thinking、工具调用/结果、图片、能力推荐（capability-prefetch）与无正文空壳",
        "only": ("role:user", "role:assistant"),
        "exclude": ("type:thinking", "type:toolCall", "role:toolResult",
                    "type:image", "customType:capability-prefetch"),
        "max_chars": 0,
        "require_body": True,
    },
    "trace": {
        "purpose": "跟踪对话与执行：保留对话、工具调用发起与行为推荐/提醒",
        "hidden": "隐藏 thinking、工具结果与图片数据",
        "only": ("role:user", "role:assistant", "type:toolCall",
                 "customType:capability-prefetch", "customType:capability-trajectory-warning"),
        "exclude": ("type:thinking", "role:toolResult", "type:image"),
        "max_chars": DEFAULT_MAX_CHARS,
    },
}
TAG_FIELDS = ("type", "role", "customType")
CONTEXT_FIELDS = frozenset((*TAG_FIELDS, "id", "parentId", "timestamp"))
OMIT = object()  # Unlike None, this cannot be a JSON value.
LIST_LIMIT = 30

# 会话根目录: --session-dir 参数指定，默认 ~/.pi/agent/sessions。
# 参数名与 pi 本体的 --session-dir 对齐。
DEFAULT_SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".pi", "agent", "sessions")

# ── 输出强制 UTF-8（Windows 管道 / GBK 控制台下不炸）────────────────────────
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TTY = sys.stdout.isatty()

def _c(code, s):
    return f"\x1b[{code}m{s}\x1b[0m" if TTY else str(s)

def dim(s):    return _c("2", s)
def bold(s):   return _c("1", s)
# ── 基础工具 ─────────────────────────────────────────────────────────────────
def flatten(t):
    """换行/连续空白折叠成单个空格。"""
    return " ".join(str(t).split())

def trunc(t, n):
    """列表预览折成单行，省略处标字符数。"""
    t = flatten(t)
    return t if len(t) <= n else t[:n] + f" …(+{len(t) - n}c)"

def die(msg, code=1):
    print(f"pi-snap: {msg}", file=sys.stderr)
    sys.exit(code)

def is_default_dir(sessions_dir):
    return not sessions_dir or os.path.abspath(sessions_dir) == os.path.abspath(DEFAULT_SESSIONS_DIR)

def copy_prefix(sessions_dir):
    """复制即用的命令前缀: 自定义 root 时带上 --session-dir(路径含空格也引用好)。"""
    if is_default_dir(sessions_dir):
        return "pi-snap "
    return f"pi-snap --session-dir {shlex.quote(sessions_dir)} "

# ── 会话定位 ─────────────────────────────────────────────────────────────────
def session_files(sessions_dir):
    """递归找 sessions_dir 下所有 .jsonl(含直接平铺在根下的)。
    pi 自己的布局是 <dir>/<munged-cwd>/xxx.jsonl 单层，递归是它的超集。"""
    if not os.path.isdir(sessions_dir):
        return []
    return glob.glob(os.path.join(sessions_dir, "**", "*.jsonl"), recursive=True)

def session_id_of(path):
    """文件名 <时间戳>_<uuid>.jsonl 的 uuid 部分。"""
    base = os.path.basename(path)
    if base.endswith(".jsonl"):
        base = base[:-6]
    i = base.rfind("_")
    return base[i + 1:] if i >= 0 else base

def name_matches(path, prefix):
    """文件名入口: uuid 部分或整段文件名前缀。"""
    stem = os.path.basename(path)
    if stem.endswith(".jsonl"):
        stem = stem[:-6]
    return session_id_of(path).startswith(prefix) or stem.startswith(prefix)

def find_session(prefix, sessions_dir):
    """定位唯一会话文件: 直接路径 / 文件名前缀 / 会话头里的 id 前缀。

    文件名(时间戳_uuid.jsonl)和会话头 id 两条路径的命中取并集，每个文件只看
    一次：文件名没命中的才做一次有界的头部读取取 id(只要 id，不读正文)——
    每个 Run 目录下都叫 session.jsonl 的布局靠它定位。两条路径各命中一个
    不同文件时算歧义，不静默选其中一个。"""
    if os.path.isfile(prefix):
        return prefix
    matches, sid_of = [], {}
    for p in session_files(sessions_dir):
        if name_matches(p, prefix):
            matches.append(p)
            continue
        sid = peek_head(p, preview=False)[0]
        if sid.startswith(prefix):
            sid_of[p] = sid
            matches.append(p)
    if not matches:
        die(f"找不到会话 {prefix!r} (会话目录: {sessions_dir})\n"
            "不带参数运行 pi-snap 可列出所有会话。")
    if len(matches) > 1:
        print("该前缀匹配多个会话，请加长前缀或用完整路径指定:")
        for p in sorted(matches, key=os.path.getmtime, reverse=True):
            dt = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m-%d %H:%M")
            print(f"  {dt}  {sid_of.get(p) or peek_head(p, preview=False)[0]}  {p}")
        sys.exit(2)
    return matches[0]

# ── 加载与分支 ───────────────────────────────────────────────────────────────
def load_entries(path, strict=False):
    """查看时跳过坏行；导出时拒绝损坏或非对象记录，避免静默丢失上下文。"""
    entries = []
    bad = 0
    with open(path, encoding="utf-8", errors="strict" if strict else "replace") as fh:
        for line_number, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    if strict:
                        die(f"第 {line_number} 行不是记录对象，无法分叉")
                    bad += 1
                    continue
                entries.append(entry)
            except ValueError:
                if strict:
                    die(f"第 {line_number} 行 JSON 损坏，无法分叉；请等待写入完成或使用完整记录")
                bad += 1
    if bad:
        print(dim(f"(跳过 {bad} 行无法解析的记录)"), file=sys.stderr)
    return entries

def active_branch(entries):
    """当前分支: 文件最后一条记录沿 parentId 回溯到根，返回(根→尾, id→记录)。"""
    by_id = {e["id"]: e for e in entries if isinstance(e, dict) and "id" in e}
    chain, cur, seen = [], entries[-1] if entries else None, set()
    while isinstance(cur, dict) and cur.get("id") not in seen:
        seen.add(cur.get("id"))
        chain.append(cur)
        cur = by_id.get(cur.get("parentId"))
    chain.reverse()
    return chain, by_id

def export_fork(entries, source, ref, destination):
    """按 parentId 提取包含截止点的路径；不拼接旁支，也不执行工具。"""
    header = entries[0]
    if (header.get("type") != "session" or header.get("version") != 3
            or not isinstance(header.get("id"), str) or not header["id"]):
        die("分叉需要 Pi v3 session 头部")
    by_id = {}
    for entry in entries[1:]:
        eid = entry.get("id")
        parent = entry.get("parentId")
        if (entry.get("type") == "session" or not isinstance(eid, str) or not eid
                or eid in by_id or eid == header["id"] or "parentId" not in entry
                or (parent is not None and not isinstance(parent, str))):
            die("记录 id 重复、缺失或 parentId 无效，无法分叉")
        by_id[eid] = entry
    current = find_entry(entries, ref)
    if current is header:
        die("分叉截止点必须是会话头之后的记录")
    chain, seen = [], set()
    while current is not None:
        eid = current["id"]
        if eid in seen:
            die("parentId 存在循环，无法分叉")
        seen.add(eid)
        chain.append(current)
        parent = current["parentId"]
        if parent is not None and parent not in by_id:
            die(f"缺少父记录 {parent!r}，无法分叉")
        current = by_id.get(parent)
    chain.reverse()

    # 所有工具返回必须来自本分支的请求；不能从并行调用的中途导出。
    pending, preceding = set(), set()
    for entry in chain:
        preceding.add(entry["id"])
        if entry.get("type") == "compaction":
            kept = entry.get("firstKeptEntryId")
            if not isinstance(kept, str) or kept not in preceding:
                die("压缩记录 firstKeptEntryId 不在此前路径中，无法分叉")
        if entry.get("type") != "message":
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            die("message 记录无效，无法分叉")
        if msg.get("role") == "assistant":
            if pending:
                die("此前工具调用缺少返回，无法分叉")
            content = msg.get("content") or []
            for part in content if isinstance(content, list) else []:
                if isinstance(part, dict) and part.get("type") == "toolCall":
                    call_id = part.get("id")
                    if not isinstance(call_id, str) or not call_id or call_id in pending:
                        die("工具调用 id 缺失或重复，无法分叉")
                    pending.add(call_id)
        elif msg.get("role") == "toolResult":
            call_id = msg.get("toolCallId")
            if not isinstance(call_id, str) or call_id not in pending:
                die("工具返回缺少匹配的调用，无法分叉")
            pending.remove(call_id)
        elif msg.get("role") == "user" and pending:
            die("此前工具调用缺少返回，无法分叉")
    if pending:
        die("截止点仍有工具调用缺少返回；请选择调用前或所有返回之后的记录")

    fork_header = dict(header, id=str(uuid4()),
                       timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                       parentSession=os.path.abspath(source))
    # x 模式原子地拒绝已有路径（也保护源文件及指向源文件的链接）。
    output = open(destination, "x", encoding="utf-8", newline="\n")
    try:
        with output:
            output.write(json.dumps(fork_header, ensure_ascii=False) + "\n")
            for entry in chain:
                output.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except BaseException:
        os.unlink(destination)
        raise
    print(os.path.abspath(destination))


def select_range(entries, seq, since, before):
    """Resolve IDs before view filtering. Boundaries must belong to this branch."""
    bounds = []
    for ref, default in ((since, 0), (before, len(seq))):
        if ref is None:
            bounds.append(default)
            continue
        entry = find_entry(entries, ref)
        index = next((i for i, item in enumerate(seq) if item is entry), None)
        if index is None:
            die(f"记录 {ref!r} 不在当前分支，不能用作区间边界")
        bounds.append(index)
    start, end = bounds
    if start > end:
        die("--since 起点在 --before 终点之后")
    return seq[start:end]


# ── 时间语义（独立于类型筛选与渲染）──────────────────────────────────────────
def parse_ts(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        if not isinstance(value, str):
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Do not interpret a timestamp with an unknown timezone as local time.
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def format_ts(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def record_clock(record):
    """Pi message time and known host execution times, never guessed from prose.

    A preparation batch is one source record, with several actual event times.
    Its outer timestamp is persistence provenance, NOT an execution fallback.
    """
    message = record.get("message")
    body = message if isinstance(message, dict) else record
    persisted = parse_ts(record.get("timestamp"))
    if body.get("customType") != "capability-prefetch":
        stamp = parse_ts(body.get("timestamp")) if "timestamp" in body else persisted
        return {"times": [stamp] if stamp else [], "missing": stamp is None,
                "preparation": False, "persisted": persisted}
    details = body.get("details")
    prefetch = details.get("prefetch") if isinstance(details, dict) else None
    rounds = prefetch.get("rounds") if isinstance(prefetch, dict) else None
    stamps = []
    for batch in rounds if isinstance(rounds, list) else []:
        messages = batch.get("messages") if isinstance(batch, dict) else None
        for item in messages if isinstance(messages, list) else []:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            is_call = item.get("role") == "assistant" and isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") == "toolCall" for part in content)
            if is_call or item.get("role") == "toolResult":
                stamps.append(parse_ts(item.get("timestamp")))
    return {"times": [stamp for stamp in stamps if stamp is not None],
            "missing": not stamps or any(stamp is None for stamp in stamps),
            "preparation": True, "persisted": persisted}


def clock_label(clock):
    times = clock["times"]
    label = "执行" if clock["preparation"] else "时间"
    if times:
        first, last = min(times), max(times)
        result = f"[{label} {format_ts(first)}"
        if last != first:
            result += f" … {format_ts(last)}"
        result += "]"
        if clock["missing"]:
            result += " [部分执行时间未知]"
    else:
        result = f"[{label}时间未知]" if label == "执行" else "[时间未知]"
    if clock["preparation"]:
        persisted = clock["persisted"]
        result += f" [落盘 {format_ts(persisted) if persisted else '未知'}]"
        if times and (times != sorted(times) or (persisted and max(times) > persisted)):
            result += " [时间冲突；保留原位置]"
    return result


def timed_records(entries, reorder=True):
    """Attach clocks and safely move delayed batches along their own ancestry.

    Preserve each complete source record, unlike the former lossy tool-message
    projection. Ordinary records never reorder; no move crosses a user/context
    boundary, another preparation batch, or a raw-ID range boundary. An unknown
    or conflicting clock cannot establish ordering.
    """
    rows = [(entry, record_clock(entry)) for entry in entries]
    by_id = {entry.get("id"): i for i, (entry, _) in enumerate(rows)}
    buckets = [[] for _ in rows]
    barriers = {"compaction", "context_edit", "branch_summary"}
    for index, (entry, clock) in enumerate(rows):
        times, persisted = clock["times"], clock["persisted"]
        target = index
        if (reorder and clock["preparation"] and not clock["missing"] and persisted
                and times == sorted(times) and times[-1] <= persisted):
            cursor, candidate, seen = entry.get("parentId"), index, set()
            while cursor in by_id and cursor not in seen:
                seen.add(cursor)
                pos = by_id[cursor]
                if pos >= index:
                    break
                ancestor, ancestor_clock = rows[pos]
                known = ancestor_clock["times"] if not ancestor_clock["missing"] else []
                when = max(known) if known else None
                if when and when <= times[0]:
                    target = candidate
                    break
                message = ancestor.get("message")
                body = message if isinstance(message, dict) else ancestor
                if (ancestor.get("type") in barriers or body.get("role") in ("user", "system")
                        or ancestor_clock["preparation"] or not when or when < times[-1]):
                    break
                candidate, cursor = pos, ancestor.get("parentId")
        if target < index:
            buckets[target].insert(0, (entry, clock))
        else:
            buckets[index].append((entry, clock))
    for bucket in buckets:
        yield from bucket


# ── 结构筛选与统一渲染 ───────────────────────────────────────────────────────
def parse_selectors(values):
    """A selector is a raw tag value, optionally qualified by its JSON key."""
    selectors = []
    for group in values:
        for token in group.split(","):
            token = token.strip()
            if not token:
                raise ValueError("类型筛选不能为空")
            if ":" in token:
                key, value = token.split(":", 1)
                if key not in TAG_FIELDS or not value:
                    raise ValueError("限定筛选使用 type:值、role:值 或 customType:值")
                selectors.append((key, value))
            else:
                selectors.append((None, token))
    return tuple(selectors)


def resolve_options(view=None, only=None, exclude=None, max_chars=None):
    """Compose a view's defaults with explicit overrides into ordinary options.

    --only replaces the view inclusion list; --exclude appends to its
    exclusions; --max-chars overrides its length. Without a view, no filtering.
    """
    if view is None:
        base = {}
    elif view in VIEWS:
        base = VIEWS[view]
    else:
        raise ValueError(f"未知视图 {view!r}；可选: {', '.join(sorted(VIEWS))}")
    return {
        "only": parse_selectors(base.get("only", ()) if only is None else only),
        "exclude": parse_selectors((*base.get("exclude", ()), *(exclude or ()))),
        "max_chars": base.get("max_chars", DEFAULT_MAX_CHARS) if max_chars is None else max_chars,
        "require_body": bool(base.get("require_body")),
    }


def node_tags(node):
    """String-valued TAG_FIELDS on one node; shared by match, discovery, display."""
    if not isinstance(node, dict):
        return ()
    return tuple((key, node[key]) for key in TAG_FIELDS if isinstance(node.get(key), str))


def matches(node, selectors):
    tags = dict(node_tags(node))
    return any(tags.get(key) == value if key else value in tags.values()
               for key, value in selectors)


def tag_chain(*nodes):
    """Qualified tag ancestry along the rendering path; never invented nodes."""
    links = ["+".join(f"{key}:{value}" for key, value in node_tags(node))
             for node in nodes if node_tags(node)]
    return " > ".join(links) or "record"


def filter_record(record, only=(), exclude=()):
    """Project structural tags, without changing sources or inventing events.

    Include opens a whole subtree; exclude always wins. Ancestors of a match
    retain identity/context fields, not unrelated sibling payloads. Lists keep
    the order of surviving items, including null/false/empty values.
    """
    if not only and not exclude:
        return record
    message = record.get("message") if isinstance(record, dict) else None
    if record.get("type") == "message" and isinstance(message, dict) and matches(message, exclude):
        return OMIT  # A message-level exclusion must not leave an empty envelope row.

    def visit(value, included):
        if isinstance(value, dict):
            if matches(value, exclude):
                return OMIT
            included = included or matches(value, only)
            result = {}
            for key, child in value.items():
                selected = visit(child, included)
                if selected is not OMIT:
                    result[key] = selected
            if included:
                return result
            if not result:
                return OMIT
            return {key: result[key] if key in result else child
                    for key, child in value.items()
                    if key in result or key in CONTEXT_FIELDS}
        if isinstance(value, list):
            result = []
            for child in value:
                selected = visit(child, included)
                if selected is not OMIT:
                    result.append(selected)
            return result if included or result else OMIT
        return value if included else OMIT

    return visit(record, not only)


def value_lines(value, indent=0, label=None):
    """Full entry details: every JSON field, with terminal controls escaped."""
    pad = " " * indent
    prefix = pad + (label + ":" if label is not None else "-")
    if isinstance(value, (dict, list)):
        if not value:
            yield prefix + (" {}" if isinstance(value, dict) else " []")
            return
        yield prefix
        if isinstance(value, dict):
            for key, child in value.items():
                yield from value_lines(child, indent + 2,
                                       json.dumps(key, ensure_ascii=False))
        else:
            for child in value:
                yield from value_lines(child, indent + 2)
        return
    if isinstance(value, str):
        if "\n" in value:
            yield prefix + " |"
            for line in value.split("\n"):
                yield pad + "  " + json.dumps(line, ensure_ascii=False)[1:-1]
            return
    annotation = ""
    if label == '"timestamp"' and isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = parse_ts(value)
        if stamp is not None:
            annotation = " [UTC " + format_ts(stamp) + "]"
    yield prefix + " " + json.dumps(value, ensure_ascii=False) + annotation


# Only established protocol metadata is detail-only. Unknown fields use the
# generic fallback; tool names never select different formatting policies.
MESSAGE_METADATA = frozenset({"api", "provider", "model", "usage", "responseId",
                              "rawStopReason", "durationMs", "thinkingLevel", "toolCallId"})
BLOCK_METADATA = frozenset({"textSignature", "thinkingSignature"})


def one_line(value):
    text = flatten(value)
    return "".join(f"\\u{ord(char):04x}" if ord(char) < 32 or ord(char) == 127 else char
                   for char in text)


def inline_value(value):
    if isinstance(value, str):
        return one_line(value) or '""'
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def inline_fields(fields):
    return ", ".join(f"{one_line(key)}={inline_value(value)}" for key, value in fields.items())


def preparation_content(body):
    """Separate saved host messages for readable rows without changing sources.

    Suggestions and unknown nested objects stay ordinary data, not tool events.
    Empty containers created by extraction are omitted from the summary only.
    """
    if body.get("customType") != "capability-prefetch":
        return body, []
    details = body.get("details")
    prefetch = details.get("prefetch") if isinstance(details, dict) else None
    rounds = prefetch.get("rounds") if isinstance(prefetch, dict) else None
    if not isinstance(rounds, list):
        return body, []
    messages, remaining = [], []
    for batch in rounds:
        if not isinstance(batch, dict) or not isinstance(batch.get("messages"), list):
            remaining.append(batch)
            continue
        rest, other = dict(batch), []
        for item in batch["messages"]:
            if isinstance(item, dict) and item.get("role") in ("assistant", "toolResult"):
                messages.append(item)
            else:
                other.append(item)
        if other:
            rest["messages"] = other
        else:
            rest.pop("messages")
        if rest:
            remaining.append(rest)
    pref = dict(prefetch)
    if remaining:
        pref["rounds"] = remaining
    else:
        pref.pop("rounds")
    detail = dict(details)
    if pref:
        detail["prefetch"] = pref
    else:
        detail.pop("prefetch")
    result = dict(body)
    if detail:
        result["details"] = detail
    else:
        result.pop("details")
    return result, messages


def projected_has_body(record):
    """View-level empty-shell check (story); other records always keep rows.

    Only message-shaped records can reduce to an empty content envelope when
    every part was filtered out. The check belongs to view configuration, so
    custom --only/--exclude keep composing on top and default/custom views
    still show their own rows unchanged.
    """
    if not isinstance(record, dict):
        return True
    message = record.get("message")
    if record.get("type") == "message" and isinstance(message, dict):
        body = message
    elif record.get("type") == "custom_message":
        body = record
    else:
        return True
    body, prepared = preparation_content(body)
    content = body.get("content", OMIT)
    if isinstance(content, list):
        return bool(content) or bool(prepared)
    return True


def record_rows(record, clock=None, head=None):
    """Yield (chain, body, clock) using basic content shapes plus one fallback.

    The chain is the real tag ancestry (record -> message -> part) taken from
    the source; extracted preparation rows keep their host record tags instead
    of masquerading as top-level assistant messages. Tool names live in the
    body (call arrow / result arrow).
    """
    clock = record_clock(record) if clock is None else clock
    message = record.get("message")
    wrapped = record.get("type") == "message" and isinstance(message, dict)
    raw_body = message if wrapped else record
    if head is None:
        head = (record, raw_body) if wrapped else (record,)
    body, prepared = preparation_content(raw_body)
    is_message = wrapped or record.get("type") == "custom_message"
    result_message = is_message and body.get("role") == "toolResult"
    metadata = MESSAGE_METADATA if wrapped else frozenset()
    consumed = (CONTEXT_FIELDS | metadata | {"content"}) - {
        key for key in TAG_FIELDS if key in body and not isinstance(body[key], str)}
    if result_message:
        consumed = consumed | {"toolName"}
    extras = {key: value for key, value in body.items() if key not in consumed}
    outer = {key: value for key, value in record.items()
             if key not in CONTEXT_FIELDS and key != "message"} if wrapped else {}
    rows = []
    content = body.get("content", OMIT)
    parts = content if isinstance(content, list) else [content] if content is not OMIT else []
    for part in parts:
        chain_nodes = head
        if not isinstance(part, dict):
            text = inline_value(part)
        else:
            chain_nodes = head + (part,)
            kind = part.get("type")
            consumed_part = {"type"} if isinstance(kind, str) else set()
            if kind in ("text", "thinking") and isinstance(part.get(kind), str):
                text = inline_value(part[kind])
                consumed_part = consumed_part | BLOCK_METADATA | {kind}
            elif kind == "toolCall":
                args = part.get("arguments", OMIT)
                args_text = inline_fields(args) if isinstance(args, dict) else (
                    "arguments=" + inline_value(args) if args is not OMIT else "")
                text = "→ " + str(part.get("name", "?")) + ((" " + args_text) if args_text else "")
                consumed_part = consumed_part | {"id", "name", "arguments"}
            else:
                # Tool-result blocks retain result attribution, including images.
                text = ""
            extra = inline_fields({key: value for key, value in part.items() if key not in consumed_part})
            text = " · ".join(value for value in (text, extra) if value)
        if result_message:
            text = " ".join(value for value in ("← " + str(body.get("toolName", "?")), text) if value)
        rows.append((tag_chain(*chain_nodes), text or "{}", clock))
    extra = inline_fields(extras)
    if outer:
        extra = ", ".join(value for value in (extra, "entry=(" + inline_fields(outer) + ")") if value)
    if rows:
        first_chain, first_text, _ = rows[0]
        rows[0] = (first_chain, " · ".join(value for value in (first_text, extra) if value), clock)
    elif extra or not prepared or content is not OMIT:
        rows.append((tag_chain(*head), extra or ("content=" + inline_value(content) if content is not OMIT else "{}"), clock))
    for item in prepared:
        child = {"type": "message", "message": item}
        # Never inherit the batch/persistence time for a message with no timestamp.
        child_clock = record_clock(child)
        for child_chain, text, when in record_rows(child, child_clock, head + (item,)):
            rows.append((child_chain, text, when))
    return rows


def short_time(stamp, ref_date=None):
    if stamp is None:
        return "--:--:--"
    local = stamp.astimezone()
    return local.strftime("%Y-%m-%d %H:%M:%S" if ref_date and local.date() != ref_date else "%H:%M:%S")


def render_record(record, number, width=4, mark="", max_chars=300, clock=None, ref_date=None):
    clock = record_clock(record) if clock is None else clock
    batch_times = clock["times"]
    batch_conflict = clock["preparation"] and batch_times and (
        batch_times != sorted(batch_times) or (clock["persisted"] and max(batch_times) > clock["persisted"]))
    for label, text, when in record_rows(record, clock):
        times = when["times"]
        stamp = min(times) if times else None
        note = ""
        if times and (when["missing"] or (clock["preparation"] and clock["missing"])):
            note = " [部分时间未知]"
        if batch_conflict:
            note += " [时间冲突]"
        text = one_line(text)
        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + f" …(+{len(text) - max_chars}c)"
        print(f"{mark}[{number:>{width}}] {short_time(stamp, ref_date)} [{one_line(label)}] {text}{note}")


def record_window(entries, show_all, since=None, before=None,
                   since_time=None, before_time=None):
    """Branch/range/time record selection shared by snapshots and discovery."""
    if show_all and (since is not None or before is not None):
        die("--all 不能与 --since 或 --before 混用，避免跨分支区间")
    chain, _ = active_branch(entries)
    root = next((e for e in entries if e.get("type") == "session"), None)
    branch = list(chain)
    if root is not None and not any(e is root for e in branch):
        branch.insert(0, root)
    selected = select_range(entries, entries if show_all else branch, since, before)
    if since_time is None and before_time is None:
        return selected, branch, root, 0
    kept, unknown = [], 0
    for record in selected:
        clock = record_clock(record)
        unknown += int(clock["missing"])
        if any((since_time is None or stamp >= since_time)
               and (before_time is None or stamp < before_time) for stamp in clock["times"]):
            kept.append(record)
    return kept, branch, root, unknown


def render_snapshot(entries, path, show_all, tail, since=None, before=None,
                    options=None, since_time=None, before_time=None):
    selected, branch, root, unknown_times = record_window(
        entries, show_all, since, before, since_time, before_time)
    options = options or resolve_options()
    numbers = {id(entry): number for number, entry in enumerate(entries, 1)}
    visible = []
    for record, clock in timed_records(selected, reorder=not show_all):
        projected = filter_record(record, options["only"], options["exclude"])
        if projected is OMIT:
            continue
        if options["require_body"] and not projected_has_body(projected):
            continue
        visible.append((projected, clock, numbers[id(record)]))
    if unknown_times:
        print(f"pi-snap: {unknown_times} 条记录部分或全部时间未知，未知部分不能按时间筛选。", file=sys.stderr)
    if tail is not None:
        visible = visible[-tail:]

    sid = (root or {}).get("id") or session_id_of(path)
    reference = parse_ts((root or {}).get("timestamp"))
    if reference is None:
        reference = next((clock["times"][0] for entry in branch
                          if (clock := record_clock(entry))["times"]), None)
    ref_date = reference.astimezone().date() if reference else None
    date_label = reference.astimezone().strftime(" · %Y-%m-%d %z") if reference else ""
    print(bold("session " + one_line(sid) + date_label))
    width = max(4, len(str(len(entries))))
    on_branch = {id(entry) for entry in branch}
    branch_numbers = {numbers[key] for key in on_branch}
    for record, clock, number in visible:
        mark = (" " if number in branch_numbers else "~") if show_all else ""
        render_record(record, number, width, mark, options["max_chars"], clock, ref_date)
    if not visible:
        print(dim("当前筛选无可显示记录。"))


def peek_head(path, max_lines=60, preview=True):
    """只读文件开头: session 条目(id/cwd) + 第一条 user 消息预览。

    有界读取(最多 max_lines 行)，不扫正文；列表和按头部 id 定位共用它。
    preview=False 时拿到头部 id 就停，不接着读 user 正文。"""
    sid, cwd, first_user = "", "", ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue
                if e.get("type") == "session" and not sid:
                    sid, cwd = e.get("id", ""), e.get("cwd", "")
                elif e.get("type") == "message" and not first_user:
                    m = e.get("message")
                    if isinstance(m, dict) and m.get("role") == "user":
                        content = m.get("content")
                        txt = content if isinstance(content, str) else " ".join(
                            str(p.get("text", "")) for p in content
                            if isinstance(p, dict) and p.get("type") == "text"
                        ) if isinstance(content, list) else ""
                        if txt.strip():
                            first_user = txt.strip()
                # 要的东西到手就在本行末尾收手：for 循环顶部的 break 会先读下一行，
                # 头部后面紧跟巨大单行(如 system 正文)时会白读进来
                if sid and (not preview or first_user):
                    break
    except OSError:
        pass
    return sid or session_id_of(path), cwd, first_user

def place_label(path, sessions_dir, cwd):
    """列表第二列: 用父目录名区分同名文件。每个 Run 目录下一个 session.jsonl
    的布局里，父目录名就是可辨认的 Run 名；pi 默认布局的父目录是转义后的 cwd
    (以 - 开头的 --D--proj-- 形式)，这种没信息量的目录名退回 cwd 尾名。"""
    parent = os.path.dirname(os.path.abspath(path))
    tail = cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] if cwd else ""
    if os.path.abspath(sessions_dir) == parent:
        return tail
    name = os.path.basename(parent)
    if name.startswith("-") and tail and tail in name:
        return tail
    return name or tail

def list_sessions(sessions_dir):
    files = session_files(sessions_dir)
    if not files:
        die(f"会话目录不存在或为空: {sessions_dir}")
    rows = []
    for p in files:                     # 每个文件只做一次有界头部读取
        sid, cwd, first_user = peek_head(p)
        rows.append((os.path.getmtime(p), sid, place_label(p, sessions_dir, cwd),
                     flatten(first_user), p))
    rows.sort(key=lambda r: -r[0])
    hdr = bold(f"{len(rows)} 个会话") + dim(f" · {copy_prefix(sessions_dir)}<头部id> 打开记录")
    if not is_default_dir(sessions_dir):
        hdr += dim(f"\n会话目录: {sessions_dir}")
    print(hdr)
    # 显示的 id 就是会话头里的完整 id，复制出来即可用(截短过就未必唯一)
    for mtime, sid, place, first_user, _p in rows[:LIST_LIMIT]:
        when = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
        print(f"{when}  {sid}  {place:<18}  {trunc(first_user, 60)}")
    if len(rows) > LIST_LIMIT:
        print(dim(f"… 共 {len(rows)} 个，仅显示最近 {LIST_LIMIT} 个"))
    counts = {}
    for r in rows:                      # 重复 id 的前缀永远分不开，只能给路径
        counts[r[1]] = counts.get(r[1], 0) + 1
    dupes = [r for r in rows if counts[r[1]] > 1]
    if dupes:
        print(dim(f"id 重复的 {len(dupes)} 个会话只能按路径打开:"))
        for r in dupes[:LIST_LIMIT]:
            print(dim("  " + copy_prefix(sessions_dir) + shlex.quote(r[4])))
        if len(dupes) > LIST_LIMIT:
            print(dim(f"  … 还有 {len(dupes) - LIST_LIMIT} 个"))

# ── 视图与类型发现 ──────────────────────────────────────────────
def list_views():
    """Views are documented once here and in VIEWS; no session required."""
    print(bold("views") + dim(" · pi-snap SESSION --view NAME；不指定 --view 时完整展示"))
    for name, view in VIEWS.items():
        print(f"{bold(name)}  {view['purpose']}；{view['hidden']}")
        print(dim(f"  --only {','.join(view['only'])}"
                  f" --exclude {','.join(view['exclude'])}"
                  f" --max-chars {view['max_chars']}"))

def discover_types(records):
    """(path, selector) pairs actually present; no plugin semantics guessed.

    Shares node_tags with filtering and display. Array positions collapse to
    []; an identical path+selector pair is reported once. Strings are never
    parsed as nested JSON, and prose is never treated as an event.
    """
    found = {}

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node_tags(node):
                found.setdefault((f"{path}.{key}", f"{key}:{value}"))
            for key, child in node.items():
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for child in node:
                walk(child, f"{path}[]")

    for record in records:
        walk(record, "$")
    return sorted(found)

def list_types(records, scope):
    """Copyable qualified selectors with the actual structure paths."""
    rows = discover_types(records)
    print(bold(f"{len(rows)} 个结构标签")
          + dim(f" · {len(records)} 条记录 · {scope} · 选择器用于 --only / --exclude"))
    width = max((len(path) for path, _ in rows), default=0)
    for path, selector in rows:
        print(f"{path:<{width}}  {selector}")
    if not rows:
        print(dim("未发现 type/role/customType 标签。"))

# ── 单条完整视图 ─────────────────────────────────────────────────────────────
def find_entry(entries, ref):
    if ref.startswith("id:"):
        ref = ref[3:]
        if not ref:
            die("id: 后需要原始记录 ID", 2)
    elif ref.isascii() and ref.isdigit():
        number = int(ref)
        if 1 <= number <= len(entries):
            return entries[number - 1]
        die(f"记录序号 {ref} 超出范围 1..{len(entries)}", 2)
    exact = [e for e in entries if isinstance(e, dict) and e.get("id") == ref]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        die(f"记录 ID {ref!r} 重复，无法唯一定位", 2)
    cands = [e for e in entries if isinstance(e, dict)
             and str(e.get("id", "")).startswith(ref)]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        die(f"找不到记录 {ref!r}")
    print("该前缀匹配多条记录，请加长前缀:")
    for e in cands:
        m = e.get("message") if isinstance(e.get("message"), dict) else {}
        print(f"  {e.get('id')}  {e.get('type')}"
              + (f"/{m.get('role')}" if m.get("role") else ""))
    sys.exit(2)

def render_entry_full(e, on_branch, as_json, number=None):
    if as_json:
        print(json.dumps(e, ensure_ascii=False, indent=2))
        return
    location = "当前分支" if on_branch else "已回退丢弃"
    print(bold(f"entry {number if number is not None else '?'} · id={one_line(e.get('id', '?'))} · {location}"))
    print(dim(clock_label(record_clock(e))))
    for key, value in e.items():
        for line in value_lines(value, label=json.dumps(key, ensure_ascii=False)):
            print(line)

# ── 入口 ─────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pi-snap",
        description="通用 Pi 会话记录读取、筛选与展示。默认紧凑单行，按序号展开完整记录；不修改源会话。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  pi-snap --list-views                 # 查看两种视图及默认筛选\n"
               "  pi-snap SESSION --view story         # 通读对话主线，正文完整不截断\n"
               "  pi-snap SESSION --view trace         # 对话、调用发起与行为推荐\n"
               "  pi-snap SESSION --list-types         # 实际结构标签路径与可复制选择器\n"
               "  pi-snap SESSION --only user,assistant --exclude thinking,image\n"
               "  pi-snap SESSION --view story --since ID1 --before ID2\n"
               "  pi-snap SESSION ENTRY --json\n"
               "  pi-snap SESSION ENTRY --fork ./retry.jsonl\n\n"
               "类型匹配对象的 type / role / customType，可用 role:user 等限定字段；行首标签链显示真实来源嵌套。\n"
               "筛选递归作用于嵌套对象；排除优先。视图只是筛选与长度默认值，不足之处可用 --only/--exclude/--max-chars 调整。\n"
               "序号先于筛选生成，至少4位右对齐。预取使用执行时间，完整时间在详情中查看。输出未经脱敏。")
    ap.add_argument("session", nargs="?", help="会话头 id / 文件名前缀，或 JSONL 文件路径")
    ap.add_argument("entry", nargs="?", help="记录序号或原始 ID／唯一前缀；纯数字原始 ID 使用 id: 前缀")
    ap.add_argument("--session-dir", metavar="PATH", help="会话根目录（默认 ~/.pi/agent/sessions）")
    ap.add_argument("--all", action="store_true", help="包含其他分支，保持文件顺序")
    ap.add_argument("--view", choices=sorted(VIEWS), metavar="VIEW",
                    help="视图 story(通读对话主线) 或 trace(对话/调用发起/行为推荐)；不指定则完整展示")
    ap.add_argument("--list-views", action="store_true", help="列出视图名称与说明，不需要 session")
    ap.add_argument("--list-types", action="store_true", help="列出实际结构标签路径与限定选择器；先于视图/筛选发现，可配 --all 与区间")
    ap.add_argument("--only", action="append", metavar="TYPES", help="只选指定类型，逗号分隔，可重复；替换视图 only")
    ap.add_argument("--exclude", action="append", metavar="TYPES", help="排除指定类型及其子树，逗号分隔，可重复；追加到视图 exclude")
    ap.add_argument("--max-chars", type=int, metavar="N", help="每行正文最多 N 个字符（默认 300，story 为 0 不限）；定位列不截断")
    ap.add_argument("--since", metavar="REF", help="从当前分支记录序号或原始 ID 开始，包含该记录")
    ap.add_argument("--before", metavar="REF", help="在当前分支记录序号或原始 ID 之前结束，不含该记录")
    ap.add_argument("--since-time", metavar="ISO", help="事件/执行时间起点（包含）；ISO 8601，必须带时区")
    ap.add_argument("--before-time", metavar="ISO", help="事件/执行时间终点（不含）；批次内任一事件命中即保留原记录")
    ap.add_argument("--tail", type=int, metavar="N", help="筛选后最后 N 条原始记录（正整数），不是内容块数")
    ap.add_argument("--json", action="store_true", help="配合 entry: 输出原始 JSON")
    ap.add_argument("--fork", metavar="PATH", help="导出截至 entry(含)的独立 v3 会话；目标必须不存在，不恢复环境")
    args = ap.parse_args(argv)

    display_options = (args.only is not None or args.exclude is not None
                       or args.view is not None or args.max_chars is not None)
    selection_options = (args.tail is not None or args.all
                         or args.since is not None or args.before is not None
                         or args.since_time is not None or args.before_time is not None)
    if args.list_views:
        if args.session or args.entry or display_options or selection_options \
                or args.json or args.fork is not None:
            ap.error("--list-views 不需要 session，也不能与其他参数混用")
    elif args.list_types:
        if args.entry or args.json or args.fork is not None or args.tail is not None:
            ap.error("--list-types 不能与单条、--json、--fork 或 --tail 混用")
        if display_options:
            ap.error("--list-types 先于视图与筛选发现类型，不能与 --view、--only、--exclude 或 --max-chars 混用")
        if not args.session:
            ap.error("--list-types 需要指定 session")
    if args.fork is not None:
        if not args.session or not args.entry:
            ap.error("--fork 需要指定 session 和 entry id")
        if args.json or display_options or selection_options:
            ap.error("--fork 不能与 --json、视图、筛选、长度或区间参数一起使用")
    if args.json and not args.entry:
        ap.error("--json 需要指定 entry id")
    if (display_options or selection_options) and not args.session:
        ap.error("视图、筛选、长度与区间参数需要指定 session")
    if args.entry and (display_options or selection_options):
        ap.error("单条详情不能与视图、筛选、长度或区间参数一起使用")
    if args.tail is not None and args.tail <= 0:
        ap.error("--tail 必须是正整数")
    if args.max_chars is not None and args.max_chars < 0:
        ap.error("--max-chars 必须是非负整数（0 表示不限）")
    if args.all and (args.since is not None or args.before is not None):
        ap.error("--all 不能与 --since 或 --before 混用，避免跨分支区间")
    since_time, before_time = None, None
    if args.since_time is not None:
        since_time = parse_ts(args.since_time)
        if since_time is None:
            ap.error("--since-time 需要带时区的 ISO 8601 时间，例如 2026-10-13T08:00:00+08:00")
    if args.before_time is not None:
        before_time = parse_ts(args.before_time)
        if before_time is None:
            ap.error("--before-time 需要带时区的 ISO 8601 时间，例如 2026-10-13T08:05:00+08:00")
    if since_time is not None and before_time is not None and since_time > before_time:
        ap.error("--since-time 起点在 --before-time 终点之后")
    try:
        options = resolve_options(args.view, args.only, args.exclude, args.max_chars)
    except ValueError as exc:
        ap.error(str(exc))

    sessions_dir = (os.path.expanduser(args.session_dir)
                    if args.session_dir else DEFAULT_SESSIONS_DIR)

    if args.list_views:
        list_views()
        return
    if not args.session:
        list_sessions(sessions_dir)
        return

    path = find_session(args.session, sessions_dir)
    try:
        entries = load_entries(path, strict=args.fork is not None)
        if not entries:
            die(f"会话文件为空: {path}")
        if args.fork is not None:
            export_fork(entries, path, args.entry, os.path.expanduser(args.fork))
            return
    except (OSError, UnicodeError) as exc:
        die(str(exc))

    if args.list_types:
        selected, _, _, unknown = record_window(entries, args.all, args.since, args.before,
                                               since_time, before_time)
        if unknown:
            print(f"pi-snap: {unknown} 条记录部分或全部时间未知，未知部分不能按时间筛选。", file=sys.stderr)
        list_types(selected, "全部记录" if args.all else "当前分支")
        return

    if args.entry:
        chain, _ = active_branch(entries)
        on_branch = {id(c) for c in chain}
        e = find_entry(entries, args.entry)
        render_entry_full(e, id(e) in on_branch
                          or (e is entries[0] and e.get("type") == "session"), args.json,
                          next(i for i, entry in enumerate(entries, 1) if entry is e))
        return

    render_snapshot(entries, path, show_all=args.all, tail=args.tail,
                    since=args.since, before=args.before, options=options,
                    since_time=since_time, before_time=before_time)

if __name__ == "__main__":
    main()
