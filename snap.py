#!/usr/bin/env python3
# pi-snap — Pi Agent 会话执行快照与分叉导出（不修改原会话，不依赖 pi 运行时）。
#
# 会话记录: ~/.pi/agent/sessions/<cwd 目录名>/<时间戳>_<uuid>.jsonl
# 每行一个 JSON 对象(记录)，id/parentId 构成一棵树；回退(rewind)后继续是往树上
# 追加新分支，"当前分支" = 从文件最后一条记录沿 parentId 走回根。
#
# 用法:
#   pi-snap                          列出最近会话(按修改时间倒序，第二列是父目录/Run 名，
#                                    第三列是完整头部 id)
#   pi-snap <session-id 前缀>        执行快照: 只看当前分支，内容大幅精简
#                                    <session-id> 可以是会话头里的 id、文件名
#                                    (<时间戳>_<uuid>.jsonl)前缀，或 jsonl 路径
#   pi-snap <session> --all          连被回退丢弃的记录一起看(~ 前缀标记)
#   pi-snap <session> --tail N       只看最后 N 条记录(大 session 配合管道用)
#   pi-snap <session> <entry-id>     查看某条记录的完整内容(快照中被截断的原文)
#   pi-snap <session> <entry-id> --json   该条记录的原始 JSON
#   pi-snap <session> <entry-id> --fork out.jsonl  导出含截止点的独立会话
#   pi-snap --session-dir <path> ... 指定会话根目录(递归查找其下 .jsonl；测试时
#                                    session 不在 ~/.pi 下；也可直接传 jsonl
#                                    文件路径作为 session 参数)
#
# 每行统一 [id] [time] [type] 内容。type: [user] / [assistant](Agent 说的话，
# 没有就不出现) / [→ 工具名](调用，沿用所属 assistant 条目的 id/时间) /
# [← 工具名](结果，独立记录) / [✦ compact]。
#
# 精简规则(快照模式):
#   user 消息     正文截断 ~800 字符，换行折叠
#   assistant     正文截断 ~600 字符；thinking 丢弃
#   工具调用      一行: [→ 工具名] + 关键参数(bash 只显示命令，read 只显示路径等)
#   工具结果      一行: [← 工具名] ok/ERR + 大小 + 耗时 + 前 ~100 字符；read 成功不透内容
#   异常状态      [status] 显示 aborted/error/length 和最后保存的内容类型；错误结果保留摘要。
# 未记录结果不等于正在执行或用户打断；JSONL 没落盘的流式进度无法恢复。
#   system 消息   一行 cwd/Skills/MCP/Tools/Instructions 摘要；详情展开原文
#   model_change 等元数据条目不显示(模型/会话名进头部)
#
import argparse
import glob
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from uuid import uuid4
from string import Formatter

# ── 可调参数 ─────────────────────────────────────────────────────────────────
USER_MAX, ASST_MAX = 800, 600     # user / assistant 正文截断
ARGS_MAX, PEEK_MAX = 120, 100     # 默认工具调用参数 / 工具结果预览截断
# 工具输入摘要：max_chars 缺省用 ARGS_MAX；template 缺省展示整个参数 JSON。
# 模板按参数名取值，缺失字段为空；:size 显示内容字符数，:count 显示条目数。
# optional 片段仅在 when_any 中至少一个参数非 None 时追加；defaults 补缺失/None 字段。
# truncate="middle" 保留前 1/3 和后 2/3；默认保留开头，不解析路径。
# result 可覆盖结果模板、成功/错误预览长度；未配置的沿用下方默认值。
RESULT_TEMPLATE = "{status} {size}{duration}"  # duration 有值时自带前导空格
TOOL_SUMMARIES = {
    "bash": {"max_chars": 600, "template": "{command}"},
    "read": {
        "template": "{path}",
        "truncate": "middle",
        "optional": [{
            "when_any": ("offset", "limit"),
            "template": " @{offset!s}+{limit!s}",
            "defaults": {"offset": "?", "limit": "?"},
        }],
        "result": {"template": "{status} {lines} lines {size}", "preview_chars": 0},
    },
    "ls": {"template": "{path}", "truncate": "middle"},
    "write": {"template": "{path} · {content:size}", "truncate": "middle"},
    "edit": {"template": "{path} · {edits:count} 处修改", "truncate": "middle"},
    "grep": {"template": "{pattern} · {path}"},
    "find": {"template": "{pattern} · {path}"},
}
SUMMARY_MAX = 160                 # compaction 摘要截断
LIST_LIMIT = 30                   # 列表模式最多显示几个会话

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
def red(s):    return _c("31;1", s)
def green(s):  return _c("32", s)
def yellow(s): return _c("33", s)
def blue(s):   return _c("34", s)
def cyan(s):   return _c("36", s)

# ── 基础工具 ─────────────────────────────────────────────────────────────────
def flatten(t):
    """换行/连续空白折叠成单个空格。"""
    return " ".join(str(t).split())

def trunc(t, n, mode="head"):
    """保留 n 个原文字符，省略处标字符数；middle 偏重尾部，不解析路径。"""
    t = flatten(t)
    if len(t) <= n:
        return t
    omitted = f" …(+{len(t) - n}c)"
    if mode == "middle" and n > 0:
        head = n // 3
        return t[:head] + omitted + " " + t[-(n - head):]
    return t[:n] + omitted

def fmt_size(n):
    if n < 1000:       return f"{n}c"
    if n < 1_000_000:  return f"{n / 1000:.1f}Kc"
    return f"{n / 1_000_000:.1f}Mc"

def fmt_dur(ms):
    if ms is None or ms < 0:
        return ""
    if ms < 1000:    return f"{ms / 1000:.1f}s"
    if ms < 60_000:  return f"{ms / 1000:.0f}s"
    m, s = divmod(int(ms / 1000), 60)
    h, m = divmod(m, 60)
    if h:
        d, h = divmod(h, 24)
        return f"{d}d{h}h" if d else f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"

def parse_ts(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return None

def fmt_time(iso, ref_date=None):
    dt = parse_ts(iso)
    if not dt:
        return ""
    dt = dt.astimezone()
    if ref_date is not None and dt.date() != ref_date:
        return dt.strftime("%m-%d %H:%M:%S")
    return dt.strftime("%H:%M:%S")

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
                if strict and not isinstance(entry, dict):
                    die(f"第 {line_number} 行不是记录对象，无法分叉")
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
    current = find_entry(entries[1:], ref)
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


# ── 工具调用参数压缩 ─────────────────────────────────────────────────────────
class SummaryFormatter(Formatter):
    """模板仅增加通用的大小/计数格式，不包含工具名判断。"""
    def get_value(self, key, args, kwargs):
        return kwargs.get(key, "")

    def format_field(self, value, format_spec):
        if format_spec == "size":
            return fmt_size(len(str(value or "")))
        if format_spec == "count":
            return str(len(value or []))
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return super().format_field(flatten(text), format_spec)


SUMMARY_FORMATTER = SummaryFormatter()


def fmt_call_args(name, args):
    """配置模板选取参数；无模板或参数并非对象时保留原始参数摘要。"""
    config = TOOL_SUMMARIES.get(name, {})
    if "template" not in config or not isinstance(args, dict):
        return flatten(args if isinstance(args, str) else json.dumps(args, ensure_ascii=False))
    text = SUMMARY_FORMATTER.vformat(config["template"], (), args)
    for part in config.get("optional", ()):
        if any(args.get(key) is not None for key in part["when_any"]):
            values = {**part.get("defaults", {}),
                      **{key: value for key, value in args.items() if value is not None}}
            text += SUMMARY_FORMATTER.vformat(part["template"], (), values)
    return text

def system_summary(m, tools=None, initial=False):
    """只摘要本条保存的环境；工具总数由 parentId 分支上的增减记录重建。"""
    parts = []
    sections = m.get("sections") or {}
    if "cwd" in sections:
        parts.append("cwd=" + flatten(re.sub(r"</?cwd>", "", sections["cwd"])))
    for key, label, pattern in (
        ("skills", "Skills", r"<skill\s*>"),
        ("mcp_servers", "MCP", r"(?m)^\s*-\s+mcp__[^\s]+"),
    ):
        if key in sections:
            parts.append(f"{label}={len(re.findall(pattern, sections[key]))}")
    added, removed = m.get("toolsAdded") or [], m.get("toolsRemoved") or []
    if tools is not None or "toolsAdded" in m or "toolsRemoved" in m:
        delta = []
        if added:
            delta.append(f"+{len(added)}")
        if removed:
            delta.append(f"-{len(removed)}")
        # 初始完整工具列表无需重复标 +N；后续变更仍可定位。
        suffix = "(" + "/".join(delta) + ")" if delta and not initial else ""
        parts.append(f"Tools={len(tools) if tools is not None else '?'}{suffix}")
    if "project_context" in sections:
        count = len(re.findall(r"<project_instructions\b", sections["project_context"]))
        parts.append(f"Instructions={count}")
    content = m.get("content") or ""
    size = len(content) if isinstance(content, str) else sum(
        len(p.get("text", "")) for p in content if p.get("type") == "text")
    size += sum(len(value) if isinstance(value, str) else
                len(json.dumps(value, ensure_ascii=False)) for value in sections.values())
    if size or sections:
        parts.append(f"Prompt={fmt_size(size)}")
    # 未识别的分区仍可发现，但不展开正文。
    other = [k for k in sections if k not in
             {"preamble", "cwd", "skills", "mcp_servers", "project_context"}]
    if other or not parts and sections:
        parts.append("sections=" + ", ".join(other or sections))
    return " · ".join(parts) or "(empty)"


# ── 快照渲染 ─────────────────────────────────────────────────────────────────
def render_snapshot(entries, path, show_all, tail, sessions_dir=None, selector=None):
    chain, _ = active_branch(entries)
    if not chain:
        die("会话为空或无法解析。")

    # 头部信息。注意: session 条目(type=session)不参与 parentId 链——
    # 后续条目的 parentId 是 null，不在 session 条目上，所以要单独找。
    root = next((e for e in entries if isinstance(e, dict)
                 and e.get("type") == "session"), None) or {}
    sid = root.get("id") or session_id_of(path)
    t0 = parse_ts(root.get("timestamp")) or parse_ts(chain[0].get("timestamp"))
    t1 = parse_ts(chain[-1].get("timestamp"))
    ref_date = t0.astimezone().date() if t0 else None
    name = model = None
    turns = 0
    for e in chain:
        et = e.get("type")
        if et == "session_info" and e.get("name"):
            name = e["name"]
        elif et == "model_change":
            model = f"{e.get('provider', '?')}/{e.get('modelId', '?')}"
        elif et == "message" and (e.get("message") or {}).get("role") == "user":
            turns += 1

    span = fmt_dur((t1 - t0).total_seconds() * 1000) if t0 and t1 else ""
    print(bold(f"session {sid}"))
    line2 = ""
    if t0:
        f0 = t0.astimezone().strftime("%Y-%m-%d %H:%M")
        # 同一天结束就不重复日期
        f1 = t1.astimezone().strftime("%m-%d %H:%M" if t1.astimezone().date() != t0.astimezone().date()
                                     else "%H:%M") if t1 else "?"
        line2 = f"{f0} → {f1}" + (f" · {span}" if span else "")
    cwd = root.get("cwd", "")
    print("  ".join(x for x in (line2, cwd) if x))
    if name:
        print(f"name: {name}")
    meta = []
    if model:
        meta.append(f"model {model}")
    meta.append(f"{turns} 轮")
    on_branch = {c.get("id") for c in chain}
    off_branch = [e for e in entries if e.get("id") not in on_branch
                  and e.get("type") in ("message", "compaction")]
    meta.append(f"{len(chain)}/{len(entries)} 条记录在当前分支" if off_branch
                else f"{len(chain)} 条记录")
    if tail and tail < len(chain):
        meta.append(f"--tail {tail}")
    print(dim(" · ".join(meta)))
    print()

    seq = entries if show_all else chain
    # 在裁切 --tail 前一次索引调用和结果；默认只看当前分支，不拿旧分支的结果配对。
    pending, result_ids = {}, set()
    tool_states, system_summaries = {}, {}
    for entry in seq:
        message = entry.get("message") or {}
        tools = tool_states.get(entry.get("parentId"))
        if message.get("role") == "system":
            initial = tools is None and "toolsAdded" in message and not message.get("toolsRemoved")
            if "toolsAdded" in message or "toolsRemoved" in message:
                if tools is not None or "toolsAdded" in message:
                    tools = set(tools or ())
                    tools.difference_update(message.get("toolsRemoved") or [])
                    tools.update(t["name"] for t in message.get("toolsAdded") or [])
                    tools = frozenset(tools)
            system_summaries[entry.get("id")] = system_summary(message, tools, initial)
        tool_states[entry.get("id")] = tools
        if message.get("role") == "assistant":
            for part in message.get("content") or []:
                if part.get("type") == "toolCall" and part.get("id"):
                    pending[part["id"]] = parse_ts(entry.get("timestamp"))
        elif message.get("role") == "toolResult":
            result_ids.add(message.get("toolCallId"))
    if tail:
        seq = seq[-tail:]

    first = True
    for e in seq:
        if not isinstance(e, dict):
            continue
        mark = "" if e.get("id") in on_branch else "~"
        if not mark and not first and e.get("type") == "message" \
                and (e.get("message") or {}).get("role") == "user":
            print()  # 轮与轮之间空一行
        if e.get("type") == "compaction":
            tstr = fmt_time(e.get("timestamp", ""), ref_date)
            chead = f"{mark}{dim('[' + str(e.get('id', '?')) + ']')}"
            if tstr:
                chead += " " + dim(f"[{tstr}]")
            print(f"{chead} {yellow('[✦ compact]')}"
                  + (f"  {trunc(e.get('summary', ''), SUMMARY_MAX)}" if e.get("summary") else ""))
            first = False
            continue
        if e.get("type") != "message":
            continue
        lines = render_message(e, mark, ref_date, pending, result_ids,
                               system_summaries.get(e.get("id")))
        if lines:
            print("\n".join(lines))
            first = False

    print()
    hints = []
    if off_branch:
        hints.append(f"{len(off_branch)} 条被回退丢弃(--all 可见)")
    hints.append("看某条完整内容: "
                 + copy_prefix(sessions_dir) + shlex.quote(selector or sid) + " <entry-id>")
    print(dim("─" * 8 + " " + " · ".join(hints)))

def render_message(e, mark, ref_date, pending, result_ids, system_text=None):
    """单条 message 渲染成若干行；每行统一 [id] [time] [type] 内容。
    工具调用不是独立记录，沿用所属 assistant 条目的 id/时间；
    纯工具调用的条目不输出 [assistant] 行(它只表示 Agent 说的话)。"""
    m = e.get("message") or {}
    role = m.get("role")
    eid = e.get("id", "?")
    tstr = fmt_time(e.get("timestamp", ""), ref_date)
    # 行首统一: [id] [time] [type] 内容
    head = f"{mark}{dim('[' + str(eid) + ']')}"
    if tstr:
        head += " " + dim(f"[{tstr}]")

    if role == "system":
        return [f"{head} {yellow('[system]')}  "
                + (system_text if system_text is not None else system_summary(m))]

    if role == "user":
        out = []
        for p in m.get("content") or []:
            if p.get("type") == "text" and str(p.get("text", "")).strip():
                out.append(f"{head} {green('[user]')}  {trunc(p.get('text', ''), USER_MAX)}")
            elif p.get("type") == "image":
                out.append(f"{head} {green('[user]')}  [image]")
        return out or None

    if role == "assistant":
        out = []
        reason = m.get("stopReason")
        for p in m.get("content") or []:
            pt = p.get("type")
            if pt == "text" and str(p.get("text", "")).strip():
                out.append(f"{head} {blue('[assistant]')}  {trunc(p.get('text', ''), ASST_MAX)}")
            elif pt == "toolCall":
                config = TOOL_SUMMARIES.get(p.get("name"), {})
                args_s = trunc(fmt_call_args(p.get("name"), p.get("arguments")),
                               config.get("max_chars", ARGS_MAX), config.get("truncate", "head"))
                note = ""
                if p.get("id") not in result_ids:
                    note = " [未记录结果；是否开始执行未知]"
                    if reason in ("aborted", "error", "length"):
                        note = " [响应未完成，参数可能不完整；未记录结果]"
                out.append(f"{head} {cyan('[→ ' + p.get('name', '?') + ']')}  {dim(args_s)}{yellow(note)}")
        if reason in ("aborted", "error", "length"):
            label = {"aborted": "响应已中断（取消来源未知）",
                     "error": "响应失败", "length": "响应达到长度限制"}[reason]
            parts = m.get("content") or []
            last = next((p for p in reversed(parts)
                         if p.get("type") in ("thinking", "text", "toolCall")), {})
            kind = last.get("type")
            stage = {"thinking": "思考", "text": "文字", "toolCall": "工具请求"}.get(kind)
            detail = f"最后保存的内容：{stage}" if stage else "未保存响应内容"
            if kind in ("thinking", "text") and not last.get(kind):
                detail += "（仅空块，无正文）"
            error = trunc(m.get("errorMessage") or "", ASST_MAX)
            out.append(f"{head} {yellow('[status]')} {label}；{detail}"
                       + (f"；{error}" if error else ""))
        return out or None

    if role == "toolResult":
        name = m.get("toolName", "?")
        text = "".join(p.get("text", "") for p in (m.get("content") or [])
                       if p.get("type") == "text")
        nimg = sum(1 for p in (m.get("content") or []) if p.get("type") == "image")
        dur = ""
        t0 = pending.pop(m.get("toolCallId"), None)
        t1 = parse_ts(e.get("timestamp"))
        if t0 and t1:
            dur = fmt_dur((t1 - t0).total_seconds() * 1000)
        status = red("ERR") if m.get("isError") else "ok"
        config = TOOL_SUMMARIES.get(name, {}).get("result", {})
        meta = config.get("template", RESULT_TEMPLATE).format(
            status=status, size=fmt_size(len(text)),
            lines=text.count("\n") + int(bool(text) and not text.endswith("\n")),
            duration=" " + dur if dur else "")
        line = f"{head} {yellow('[← ' + name + ']')} {meta}"
        preview_key = "error_preview_chars" if m.get("isError") else "preview_chars"
        preview_chars = config.get(preview_key, PEEK_MAX)
        if preview_chars > 0 and text.strip():
            line += "  " + trunc(text, preview_chars)
        if nimg:
            line += dim("  [image]")
        return [line]

    return None

# ── 列表模式 ─────────────────────────────────────────────────────────────────
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
                    m = e.get("message") or {}
                    if m.get("role") == "user":
                        txt = " ".join(str(p.get("text", "")) for p in (m.get("content") or [])
                                       if p.get("type") == "text").strip()
                        if txt:
                            first_user = txt
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
    hdr = bold(f"{len(rows)} 个会话") + dim(f" · {copy_prefix(sessions_dir)}<头部id> 打开快照")
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

# ── 单条完整视图 ─────────────────────────────────────────────────────────────
def find_entry(entries, ref):
    exact = [e for e in entries if isinstance(e, dict) and e.get("id") == ref]
    if exact:
        return exact[0]
    cands = [e for e in entries if isinstance(e, dict)
             and str(e.get("id", "")).startswith(ref)]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        die(f"找不到记录 {ref!r}")
    print("该前缀匹配多条记录，请加长前缀:")
    for e in cands:
        m = e.get("message") or {}
        print(f"  {e.get('id')}  {e.get('type')}"
              + (f"/{m.get('role')}" if m.get("role") else ""))
    sys.exit(2)

def render_entry_full(e, on_branch, as_json):
    if as_json:
        print(json.dumps(e, ensure_ascii=False, indent=2))
        return
    loc = "当前分支" if on_branch else "已回退丢弃"
    print(bold(f"entry {e.get('id', '?')} · {e.get('type', '?')} "
               f"· {e.get('timestamp', '?')} · {loc}"))
    if e.get("type") != "message":
        print(json.dumps(e, ensure_ascii=False, indent=2))
        return
    m = e.get("message") or {}
    role = m.get("role", "?")
    if role == "assistant":
        print(f"  stopReason={m.get('stopReason', 'unknown')}")
        if m.get("errorMessage"):
            print(f"  errorMessage={m['errorMessage']}")
    if role == "toolResult":
        print(dim(f"  toolName={m.get('toolName')} isError={bool(m.get('isError'))}"
                  + (f" toolCallId={m.get('toolCallId')}" if m.get("toolCallId") else "")))
    if role == "system":
        for name, value in (m.get("sections") or {}).items():
            print(dim(f"── section: {name} ──"))
            print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
        for key in ("toolsAdded", "toolsRemoved"):
            if key in m:
                print(dim(f"── {key} ──"))
                print(json.dumps(m[key], ensure_ascii=False, indent=2))
    content = m.get("content") or []
    if isinstance(content, str):
        print(dim("── text ──"))
        print(content)
        return
    for p in content:
        pt = p.get("type")
        if pt == "text":
            print(dim("── text ──"))
            print(str(p.get("text", "")))
        elif pt == "thinking":
            print(dim("── thinking ──"))
            print(str(p.get("thinking", "")))
        elif pt == "toolCall":
            print(dim(f"── toolCall {p.get('id', '')} {p.get('name', '')} ──"))
            print(json.dumps(p.get("arguments") or {}, ensure_ascii=False, indent=2))
        elif pt == "image":
            print(dim("── image ──"))

# ── 入口 ─────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pi-snap",
        description="Pi Agent 会话执行快照与分叉导出(不修改原会话)。显示已保存的中断/错误状态；"
                    "未记录结果不等于正在执行或用户打断，未落盘的流式进度不可恢复。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  pi-snap                          # 列出最近会话\n"
               "  pi-snap 01a0e2d6                 # 执行快照(精简)\n"
               "  pi-snap 01a0e2d6 --all           # 含被回退丢弃的记录\n"
               "  pi-snap 01a0e2d6 505c6f39        # 看某条记录的完整内容\n"
               "  pi-snap 01a0e2d6 505c6f39 --json # 该条的原始 JSON\n"
               "  pi-snap --session-dir /tmp/test-sessions\n"
               "                                   # 指定会话根目录(测试时 session\n"
               "                                   # 不在 ~/.pi/agent/sessions 下)")
    ap.add_argument("session", nargs="?", help="会话头里的 id 前缀(或文件名前缀、jsonl 文件路径)")
    ap.add_argument("entry", nargs="?", help="记录 id(快照里 [xxxx] 括号中的)")
    ap.add_argument("--session-dir", metavar="PATH", help="会话根目录，递归查找其下"
                    " .jsonl(默认 ~/.pi/agent/sessions)")
    ap.add_argument("--all", action="store_true", help="包含被回退丢弃的分支记录")
    ap.add_argument("--tail", type=int, metavar="N", help="只看最后 N 条记录")
    ap.add_argument("--json", action="store_true", help="配合 entry: 输出原始 JSON")
    ap.add_argument("--fork", metavar="PATH", help="导出截至 entry(含)的独立 v3 会话；目标必须不存在，不恢复环境")
    args = ap.parse_args(argv)

    if args.fork is not None:
        if not args.session or not args.entry:
            ap.error("--fork 需要指定 session 和 entry id")
        if args.json or args.all or args.tail is not None:
            ap.error("--fork 不能与 --json、--all 或 --tail 一起使用")

    if args.json and not args.entry:
        ap.error("--json 需要指定 entry id")
    if args.all and not args.session:
        ap.error("--all 需要指定 session")
    if args.tail and not args.session:
        ap.error("--tail 需要指定 session")

    sessions_dir = (os.path.expanduser(args.session_dir)
                    if args.session_dir else DEFAULT_SESSIONS_DIR)

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

    if args.entry:
        chain, _ = active_branch(entries)
        on_branch = {c.get("id") for c in chain}
        e = find_entry(entries, args.entry)
        render_entry_full(e, e.get("id") in on_branch, args.json)
        return

    render_snapshot(entries, path, show_all=args.all, tail=args.tail,
                    sessions_dir=sessions_dir, selector=args.session)

if __name__ == "__main__":
    main()
