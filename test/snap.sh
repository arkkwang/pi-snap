#!/usr/bin/env bash
# pi-snap 回归: 列表 / 通用结构快照 / 分支标记 / 单条完整视图 / 错误处理。
# Run: bash test/snap.sh
# fixture 会话目录用 --session-dir 指给脚本(Windows Python 的 expanduser
# 不认 HOME，不能靠 HOME 覆盖)。
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
snap="$root/pi-snap"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

sess="$work/sessions/--test-proj--"
mkdir -p "$sess"
SD="--session-dir $work/sessions"

# fixture: 两个会话。主会话含 read/bash 工具、ERR、compaction、一次回退分叉
# (user2a 被回退，user2b 是当前分支)，末尾半行坏 JSON。
S1=aaa11111-0000-4000-8000-000000000001
S2=aaa11112-0000-4000-8000-000000000002
F1="$sess/2026-09-27T10-00-00-000Z_$S1.jsonl"
F2="$sess/2026-09-27T11-00-00-000Z_$S2.jsonl"

json() { printf '%s\n' "$1" >> "$F1"; }
json '{ "type": "session", "version": 3, "id": "'$S1'", "timestamp": "2026-09-27T10:00:00.000Z", "cwd": "D:\\proj\\test-proj" }'
json '{ "type": "model_change", "id": "m1000001", "parentId": null, "timestamp": "2026-09-27T10:00:01.000Z", "provider": "anthropic", "modelId": "test-model" }'
json '{ "type": "message", "id": "u1000001", "parentId": "m1000001", "timestamp": "2026-09-27T10:00:02.000Z", "message": { "role": "user", "content": [ { "type": "text", "text": "看看这个文件" } ] } }'
json '{ "type": "message", "id": "a1000001", "parentId": "u1000001", "timestamp": "2026-09-27T10:00:03.000Z", "message": { "role": "assistant", "content": [ { "type": "thinking", "thinking": "内部思考不应出现" }, { "type": "text", "text": "我来读一下。" }, { "type": "toolCall", "id": "tc1", "name": "read", "arguments": { "path": "/tmp/a.py", "offset": 10, "limit": 50 } } ] } }'
json '{ "type": "message", "id": "r1000001", "parentId": "a1000001", "timestamp": "2026-09-27T10:00:04.000Z", "message": { "role": "toolResult", "toolCallId": "tc1", "toolName": "read", "isError": false, "content": [ { "type": "text", "text": "line1\nline2\nline3" } ] } }'
# 回退点: user2a 之后被回退，user2b 重新分叉
json '{ "type": "message", "id": "u200000a", "parentId": "r1000001", "timestamp": "2026-09-27T10:00:05.000Z", "message": { "role": "user", "content": [ { "type": "text", "text": "被回退的消息" } ] } }'
json '{ "type": "message", "id": "u200000b", "parentId": "r1000001", "timestamp": "2026-09-27T10:00:06.000Z", "message": { "role": "user", "content": [ { "type": "text", "text": "改用 bash 跑一下" } ] } }'
json '{ "type": "message", "id": "a2000001", "parentId": "u200000b", "timestamp": "2026-09-27T10:00:07.000Z", "message": { "role": "assistant", "content": [ { "type": "toolCall", "id": "tc2", "name": "bash", "arguments": { "command": "echo hi && cat /tmp/a.py" } } ] } }'
json '{ "type": "message", "id": "r2000001", "parentId": "a2000001", "timestamp": "2026-09-27T10:00:08.000Z", "message": { "role": "toolResult", "toolCallId": "tc2", "toolName": "bash", "isError": true, "content": [ { "type": "text", "text": "cat: /tmp/a.py: No such file or directory" } ] } }'
json '{ "type": "compaction", "id": "c1000001", "parentId": "r2000001", "timestamp": "2026-09-27T10:00:09.000Z", "summary": "## Goal\n读文件然后跑 bash" }'
printf '%s' '{"type":"message","id":"partial","pare' >> "$F1"   # 坏尾行: 正在写入

# 第二个会话: 与 S1 共享前缀 aaa1111，用于歧义测试
printf '%s\n' '{ "type": "session", "version": 3, "id": "'$S2'", "timestamp": "2026-09-27T11:00:00.000Z", "cwd": "D:\\proj\\test-proj" }' > "$F2"

run() { "$snap" $SD "$@" 2>&1; }

# ── 列表: 两个会话、预览取第一条 user 消息 ──────────────────────────────────
out="$(run)"
grep -q "2 个会话" <<<"$out" || fail "list: 会话数不对"
grep -q "看这个文件" <<<"$out" || fail "list: 缺第一会话预览"
grep -q "aaa11111" <<<"$out" || fail "list: 缺会话 id"

# ── 默认快照: 原始字段、正文与 header 均保留，只有旁支不显示 ──────────────
out="$(run aaa11111)"
for expected in "session $S1" 'provider=anthropic' 'modelId=test-model' \
    '内部思考不应出现' '改用 bash 跑一下' 'path=/tmp/a.py' 'offset=10' \
    'limit=50' 'line1' 'line3' 'cat: /tmp/a.py: No such' '[  10]' \
    '读文件然后跑 bash' '跳过 1 行'; do
    grep -Fq "$expected" <<<"$out" || fail "snap: 缺 $expected"
done
grep -q '被回退的消息' <<<"$out" && fail 'snap: 回退分支不应出现'

# 显式筛选才精简；旧 flag 不再存在，argparse 按未知参数拒绝。
out="$(run aaa11111 --exclude thinking,role:toolResult)"
grep -q '内部思考不应出现\|line1\|No such file' <<<"$out" && fail 'exclude: 排除子树仍出现'
grep -q 'path=/tmp/a.py' <<<"$out" || fail 'exclude: 无关调用应保留'
set +e; out="$(run aaa11111 --trace)"; code=$?; set -e
[[ $code == 2 ]] || fail "--trace 已移除，未知参数应退出 2 (实际 $code)"
grep -q 'unrecognized arguments' <<<"$out" || fail '--trace: 应报未知参数'
set +e; run aaa11111 --preset tools >/dev/null 2>&1; code=$?; set -e
[[ $code == 2 ]] || fail "--preset 已移除，未知参数应退出 2 (实际 $code)"

# ── 视图: story 通读主线 / trace 跟踪执行 ────────────────────────────────
out="$(run aaa11111 --view story)"
grep -q '内部思考不应出现\|path=/tmp/a.py\|line1\|cat: /tmp/a.py\|读文件然后跑 bash' <<<"$out"     && fail 'story: thinking/工具/结果/摘要不应出现'
grep -q '看看这个文件' <<<"$out" || fail 'story: 用户正文缺失'
grep -q '我来读一下。' <<<"$out" || fail 'story: 调用前说明缺失'
grep -q '改用 bash 跑一下' <<<"$out" || fail 'story: 中途用户纠正被裁掉'
grep -q 'content=\[\]' <<<"$out" && fail 'story: 无正文空壳应抑制'
out="$(run aaa11111 --view trace)"
grep -q 'type:message > role:assistant > type:toolCall] → bash' <<<"$out" || fail 'trace: 调用发起缺失'
grep -q '内部思考不应出现\|No such file or directory' <<<"$out" && fail 'trace: thinking/工具结果应隐藏'
grep -q '改用 bash 跑一下' <<<"$out" || fail 'trace: 对话缺失'
# 默认视图仍完整展示(含 thinking 与结果)
out="$(run aaa11111)"
grep -q '内部思考不应出现' <<<"$out" || fail '默认视图: thinking 应完整展示'
grep -q 'No such file or directory' <<<"$out" || fail '默认视图: 工具结果应完整展示'

# ── --list-views / --list-types ──────────────────────────────────────────
out="$($snap --list-views 2>/dev/null)"
grep -q 'story' <<<"$out" || fail 'list-views: 缺 story'
grep -q 'trace' <<<"$out" || fail 'list-views: 缺 trace'
grep -q 'capability-prefetch' <<<"$out" || fail 'list-views: 应说明隐藏内容'
set +e; run aaa11111 --list-views >/dev/null 2>&1; code=$?; set -e
[[ $code == 2 ]] || fail 'list-views: 带 session 应报错'
out="$(run aaa11111 --list-types)"
grep -q '\$\.message\.content\[\]\.type' <<<"$out" || fail 'list-types: 缺内容块路径'
grep -q 'type:toolCall' <<<"$out" || fail 'list-types: 缺限定选择器'
grep -q 'role:toolResult' <<<"$out" || fail 'list-types: 缺消息角色'
out="$(run aaa11111 --all --list-types)"
grep -q 'type:message' <<<"$out" || fail 'list-types: --all 应列出全部记录类型'
set +e; run aaa11111 --list-types --view story >/dev/null 2>&1; code=$?; set -e
[[ $code == 2 ]] || fail 'list-types: 与 view 混用应报错'

# ── --all: 回退条目带 ~ 标记 ────────────────────────────────────────────────
out="$(run aaa11111 --all)"
grep -q '~\[   6\].*type:message > role:user' <<<"$out" || fail '--all: 回退条目应带 ~ 标记'
grep -q '被回退的消息' <<<"$out" || fail '--all: 回退条目正文缺失'

# ── --tail 按原始记录计数 ──────────────────────────────────────────────────
out="$(run aaa11111 --tail 2)"
grep -q '看这个文件' <<<"$out" && fail '--tail: 早于尾部的不应出现'
grep -q '\[  10\]' <<<"$out" || fail '--tail: 尾部 compact 缺'
grep -q '\[   9\]' <<<"$out" || fail '--tail: 工具结果计数不应被隐藏'
grep -q '显示 .*条记录\|省略前\|看某条完整内容\|条被回退丢弃' <<<"$out" && fail '--tail: 不应出现旧统计/footer'

# ── 单条完整视图: 被截断的原文全量 ──────────────────────────────────────────
out="$(run aaa11111 r1000001)"
grep -q "line2" <<<"$out" || fail "entry: read 结果应显示全文"
out="$(run aaa11111 a1000001)"
grep -q "内部思考不应出现" <<<"$out" || fail "entry: thinking 应完整显示"
grep -q '"offset": 10' <<<"$out" || fail "entry: toolCall 参数应完整显示"
out="$(run aaa11111 u200000a)"
grep -q "已回退丢弃" <<<"$out" || fail "entry: 回退条目应标记状态"
# --json 输出可解析回原对象(只取 stdout，坏行提示在 stderr)
out="$($snap $SD aaa11111 r1000001 --json 2>/dev/null)"
python3 -c 'import json,sys; e=json.load(sys.stdin); assert e["id"]=="r1000001" and e["message"]["content"][0]["text"]=="line1\nline2\nline3"' <<<"$out" \
    || fail "entry --json: 输出不是合法原对象"

# ── 错误处理 ─────────────────────────────────────────────────────────────────
run nnn00000 >/dev/null 2>&1 && fail "不存在的会话应非零退出"
set +e; run aaa1111 >/dev/null 2>&1; code=$?; set -e
[[ $code == 2 ]] || fail "歧义前缀应退出码 2(实际 $code)"
run aaa11111 zzz >/dev/null 2>&1 && fail "不存在的记录应非零退出"

# ── 自定义会话目录: --session-dir 覆盖默认目录 ───────────────────────────
alt="$work/alt-sessions/--alt--"
mkdir -p "$alt"
printf '%s\n' '{ "type": "session", "version": 3, "id": "'$S2'", "timestamp": "2026-09-27T11:00:00.000Z", "cwd": "D:\\proj\\alt" }' > "$alt/2026-09-27T11-00-00-000Z_$S2.jsonl"

out="$($snap --session-dir "$work/alt-sessions" 2>/dev/null)"
grep -q "1 个会话" <<<"$out" || fail "--session-dir: 列表应只有 1 个会话"
grep -q "会话目录: .*alt-sessions" <<<"$out" || fail "--session-dir: 非默认目录应显示目录"
$snap --session-dir "$work/alt-sessions" aaa11112 2>/dev/null | grep -q "session $S2"     || fail "--session-dir: 快照应从指定目录找会话"
# 递归: 更深一层 + 平铺在根下的 jsonl 都能找到
mkdir -p "$work/deep/a/b/--c--"
printf '%s\n' '{ "type": "session", "version": 3, "id": "ddd44444-0000-4000-8000-000000000004", "timestamp": "2026-09-27T12:00:00.000Z", "cwd": "D:\\deep" }' > "$work/deep/a/b/--c--/2026-09-27T12-00-00-000Z_ddd44444-0000-4000-8000-000000000004.jsonl"
printf '%s\n' '{ "type": "session", "version": 3, "id": "eee55555-0000-4000-8000-000000000005", "timestamp": "2026-09-27T13:00:00.000Z", "cwd": "D:\\flat" }' > "$work/deep/2026-09-27T13-00-00-000Z_eee55555-0000-4000-8000-000000000005.jsonl"
out="$($snap --session-dir "$work/deep" 2>/dev/null)"
grep -q "2 个会话" <<<"$out" || fail "递归: 深层+平铺应共 2 个会话"
$snap --session-dir "$work/deep" ddd44444 2>/dev/null | grep -q "session ddd44444"     || fail "递归: 深层会话应可打开"
$snap --session-dir "$work/deep" eee55555 2>/dev/null | grep -q "session eee55555"     || fail "递归: 平铺在根下的会话应可打开"

# 指定目录里找不到主 fixture 的会话
$snap --session-dir "$work/alt-sessions" aaa11111 >/dev/null 2>&1     && fail "--session-dir: 不应找到其他目录里的会话"

# 中断/空块/原始状态由 dialogue.py 覆盖，不再推断执行状态。

# ── Run 目录布局: 每个 Run 一个 session.jsonl(文件名不含 id) ────────────────
# 目录名含空格: 复制出来的命令必须引用正确。
batch="$work/all runs/sessions"
B1=01a114ba-a4c5-7367-b25f-fa491c677d52        # run-001 与 run-001-copy 头部 id 相同
B2=01a114ba-a5f1-7367-b25f-fa491c677d53
mk_run() {  # $1 目录名 $2 头部 id $3 预览文字
    mkdir -p "$batch/$1"
    {
        printf '%s\n' '{"type":"session","version":3,"id":"'"$2"'","timestamp":"2026-10-07T04:58:58.629Z","cwd":"/workspace"}'
        printf '%s\n' '{"type":"message","id":"u1","parentId":null,"timestamp":"2026-10-07T04:59:00.000Z","message":{"role":"user","content":[{"type":"text","text":"'"$3"'"}]}}'
    } > "$batch/$1/session.jsonl"
}
mk_run bookmark-v2-run-001 "$B1" "书签整理第一轮"
mk_run offline-run-002 "$B2" "离线表设计第二轮"
mk_run bookmark-v2-run-001-copy "$B1" "书签整理第一轮"
bstat() { "$snap" --session-dir "$batch" "$@"; }
# 复制出来的命令写的是裸 pi-snap(部署后由 ~/.local/bin 提供)，测试里给个同名入口
bin="$work/bin"
mkdir -p "$bin"
printf '#!/usr/bin/env bash\nexec "%s" "$@"\n' "$snap" > "$bin/pi-snap"
chmod +x "$bin/pi-snap"
export PATH="$bin:$PATH"

# 列表: 每个 Run 靠父目录名可辨认(不能全是 cwd 尾名 workspace)
out="$(bstat 2>/dev/null)"
grep -q "3 个会话" <<<"$out" || fail "Run 布局: 会话数不对"
grep -q "bookmark-v2-run-001" <<<"$out" || fail "Run 布局: 列表应显示父目录名"
grep -q "offline-run-002" <<<"$out" || fail "Run 布局: 列表应显示父目录名"
grep -q "workspace" <<<"$out" && fail "Run 布局: 列表只剩 cwd 尾名, 分不出 Run"

# 头部 id 前缀定位(文件名是 session.jsonl, 只能靠会话头里的 id)
out="$(bstat "${B2:0:12}" 2>/dev/null)"
grep -q "session $B2" <<<"$out" || fail "Run 布局: 应按头部 id 前缀定位"

# 直接文件路径: 快照 / 单条 / --json 都要能用
out="$(bstat "$batch/offline-run-002/session.jsonl" 2>/dev/null)"
grep -q "session $B2" <<<"$out" || fail "Run 布局: 直接文件路径应可用"
out="$(bstat "$batch/offline-run-002/session.jsonl" u1 --json 2>/dev/null)"
python3 -c 'import json,sys; e=json.load(sys.stdin); assert e["id"]=="u1"' <<<"$out" \
    || fail "Run 布局: 直接路径 + entry --json 应可用"

# 列表给出的复制命令: 自定义 root + 含空格路径, 复制后必须真能跑
# (Windows Python 收到的路径形式与 Git Bash 不同, 只断言末尾目录段被单引号引用住)
out="$(bstat 2>/dev/null)"
hdr_cmd="$(grep -o "pi-snap --session-dir '[^']*'" <<<"$out" | head -1)"
[[ "$hdr_cmd" == "pi-snap --session-dir '"* ]] || fail "列表: 自定义 root 命令缺 --session-dir"
[[ "$hdr_cmd" == *"all runs/sessions'" ]] \
    || fail "列表: 含空格的自定义 root 未被引用(得到 $hdr_cmd)"
eval "$hdr_cmd ${B2:0:12}" >/dev/null 2>&1 || fail "列表: 复制出的命令跑不通"

# 快照不再附带 footer；按显示序号直接定位，路径有空格仍可用。
out="$(bstat "${B2:0:12}" 2>/dev/null)"
grep -q '看某条完整内容\|省略前\|条被回退丢弃' <<<"$out" && fail '快照: 不应出现旧 footer'
out="$(bstat "$batch/bookmark-v2-run-001-copy/session.jsonl" 2 --json 2>/dev/null)"
python3 -c 'import json,sys; assert json.load(sys.stdin)["id"] == "u1"' <<<"$out"     || fail '快照: 含空格路径 + 序号详情应可用'

# 重复头部 id: 歧义提示必须给出路径, 用户才能区分
set +e; out="$(bstat 01a114ba 2>&1)"; code=$?; set -e
[[ $code == 2 ]] || fail "Run 布局: 重复 id 前缀应退出码 2(实际 $code)"
[[ "$(grep -c 'session\.jsonl' <<<"$out")" == 3 ]] \
    || fail "Run 布局: 歧义提示应列出 3 条路径"
grep -q "bookmark-v2-run-001-copy" <<<"$out" || fail "Run 布局: 歧义提示应能凭路径区分重复 id"

# 原入口保留: 时间戳_uuid 文件名前缀
"$snap" $SD "2026-09-27T10-00-00-000Z_$S1" 2>/dev/null | grep -q "session $S1" \
    || fail "文件名前缀入口应保留"

# ── 两条入口命中不同文件: 文件名前缀与会话头 id 指向不同会话, 必须是歧义 ──────
# (文件名唯一命中就返回会静默选错)
mix="$work/mix/--mix--"
MA=eeee2222-3333-4a00-8000-0000000000aa
mkdir -p "$mix" "$mix/copies/run-x"
printf '%s\n' '{"type":"session","version":3,"id":"'$MA'","timestamp":"2026-09-27T15:00:00.000Z","cwd":"D:\\proj\\mix"}' > "$mix/2026-09-27T15-00-00-000Z_$MA.jsonl"
printf '%s\n' '{"type":"session","version":3,"id":"'$MA'","timestamp":"2026-09-27T15:00:00.000Z","cwd":"/workspace"}' > "$mix/copies/run-x/session.jsonl"
set +e; out="$($snap --session-dir "$work/mix" "$MA" 2>&1)"; code=$?; set -e
[[ $code == 2 ]] || fail "混合入口: 文件名与头部 id 指向不同会话应退出码 2(实际 $code)"
grep -q "run-x.session\.jsonl" <<<"$out" || fail "混合入口: 歧义提示应给出复制会话的路径"
grep -q "2026-09-27T15-00-00-000Z_$MA.jsonl" <<<"$out" || fail "混合入口: 歧义提示应给出文件名入口那条路径"
$snap --session-dir "$work/mix" "$mix/copies/run-x/session.jsonl" 2>/dev/null | grep -q "session $MA" \
    || fail "混合入口: 重复 id 的复制会话应能用路径打开"
# 列表: 重复 id 的行给出路径命令, 且命令复制后能用
out="$($snap --session-dir "$work/mix" 2>/dev/null)"
[[ "$(grep -c 'id 重复的' <<<"$out")" == 1 ]] || fail "混合入口: 列表应提示重复 id"
dupcmd="$(grep 'run-x' <<<"$out" | sed 's/^ *//')"
[[ "$dupcmd" == *"--session-dir"* ]] || fail "混合入口: 重复 id 的路径命令应带自定义 root"
eval "$dupcmd" >/dev/null 2>&1 || fail "混合入口: 重复 id 的路径命令跑不通"

# ── 列表显示完整头部 id: 显示出来的 id 直接可用 ──────────────────────────────
# 31 个会话共享 14 字符前缀, 只显示最近 30 个: 截短的前缀不管怎么算都可能碰上
# 没显示的那个, 所以显示完整 id。
many="$work/many/--many--"
mkdir -p "$many"
for n in $(seq 1 31); do
    m=$(printf 'eeee2222-3333-4a00-8000-%012d' "$n")
    f="$many/2026-10-07T0$((n / 10))-0$((n % 10))-00-000Z_$m.jsonl"
    printf '%s\n' '{"type":"session","version":3,"id":"'$m'","timestamp":"2026-10-07T01:00:00.000Z","cwd":"D:\\proj\\many"}' > "$f"
    touch -t "2026100701$(printf '%02d' "$n")" "$f"     # 序号越大越新, 第 31 个最旧
done
out="$($snap --session-dir "$work/many" 2>/dev/null)"
grep -q "31 个会话" <<<"$out" || fail "many: 会话数不对"
grep -q "仅显示最近 30 个" <<<"$out" || fail "many: 应只显示最近 30 个"
[[ "$(grep -c 'eeee2222-3333-' <<<"$out")" == 30 ]] || fail "many: 30 行都应带完整 id"
first_id="$(grep -o 'eeee2222-3333-[0-9a-f-]*' <<<"$out" | head -1)"
[[ ${#first_id} == 36 ]] || fail "many: 列表应显示完整头部 id(得到 $first_id)"
$snap --session-dir "$work/many" "$first_id" 2>/dev/null | grep -q "session $first_id" \
    || fail "many: 显示的完整 id 应唯一定位"
set +e; $snap --session-dir "$work/many" "${first_id:0:12}" >/dev/null 2>&1; code=$?; set -e
[[ $code == 2 ]] || fail "many: 12 字符前缀本就不唯一(得到 $code), 故不能截短显示"


# ── 大尾部会话: 列表/按头部 id 定位只读头部, 不扫正文 ──────────────────────
big="$work/big/run-big"
mkdir -p "$big"
python3 - "$big/session.jsonl" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    def w(o):
        fh.write(json.dumps(o) + "\n")
    w({"type": "session", "version": 3, "id": "bbbb9999-0000-4000-8000-000000000009",
       "timestamp": "2026-10-07T04:58:58.629Z", "cwd": "/workspace"})
    w({"type": "message", "id": "u1", "parentId": None,
       "timestamp": "2026-10-07T04:59:00.000Z",
       "message": {"role": "user", "content": [{"type": "text", "text": "大尾部会话"}]}})
    pad = "y" * 4000
    for i in range(2000):                      # 约 8MB 正文
        w({"type": "message", "id": f"m{i}", "parentId": "u1",
           "timestamp": "2026-10-07T04:59:01.000Z",
           "message": {"role": "assistant", "content": [{"type": "text", "text": pad}]}})
PY
out="$($snap --session-dir "$work/big" 2>/dev/null)"
grep -q "bbbb9999" <<<"$out" || fail "大文件: 列表应显示头部 id"
$snap --session-dir "$work/big" bbbb9999 2>/dev/null | grep -q "session bbbb9999-0000-4000-8000-000000000009" \
    || fail "大文件: 应按头部 id 定位"
# 统计实际读进来的字符数: 列表和定位各读一次头部, 正文不能进来
python3 - "${snap%/*}/snap.py" "$work/big" <<'PY'
import builtins, contextlib, io, os, runpy, sys

api = runpy.run_path(sys.argv[1])
root = sys.argv[2]
real_open = builtins.open

class Counted:
    def __init__(self, fh):
        self._fh, self.chars = fh, 0
    def __iter__(self):
        return self
    def __next__(self):
        line = next(self._fh)
        self.chars += len(line)
        return line
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return self._fh.__exit__(*exc)

read = []
def counting_open(path, *a, **k):
    fh = Counted(real_open(path, *a, **k))
    read.append((str(path), fh))
    return fh

builtins.open = counting_open
try:
    out = io.StringIO()
    n0 = len(read)
    with contextlib.redirect_stdout(out):
        api["list_sessions"](root)
    assert "bbbb9999" in out.getvalue(), out.getvalue()
    assert len(read) - n0 == 1, "列表应每个文件只读一次头部"
    n1 = len(read)
    found = api["find_session"]("bbbb9999", root)
    assert len(read) - n1 == 1, "定位应每个文件只读一次头部"
finally:
    builtins.open = real_open

assert found.endswith("session.jsonl"), found
size = os.path.getsize(found)
list_chars, find_chars = read[0][1].chars, read[1][1].chars
# 列表要首个 user 预览, 定位只要头部 id(preview=False), 都不能拖出正文
assert list_chars < 50_000, f"列表读了 {list_chars} 字符(全文 {size})"
assert find_chars <= 4096, f"按 id 定位读了 {find_chars} 字符(全文 {size})"
print(f"PASS: 头部有界读取 / 每文件一次 / 大正文不读(列表 {list_chars}c, 定位 {find_chars}c / 全文 {size}c)")
PY

# ── 头部后紧跟巨大单行: preview=False 的定位不能把它读进来 ─────────────────
huge="$work/bigline/run-huge"
mkdir -p "$huge"
python3 - "$huge/session.jsonl" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "session", "version": 3,
                         "id": "ffff7777-0000-4000-8000-000000000007",
                         "timestamp": "2026-10-07T04:58:58.629Z", "cwd": "/workspace"}) + "\n")
    fh.write(json.dumps({"type": "message", "id": "s1", "parentId": None,
                         "timestamp": "2026-10-07T04:59:00.000Z",
                         "message": {"role": "system",
                                     "content": [{"type": "text", "text": "z" * 2_000_000}]}}) + "\n")
PY
$snap --session-dir "$work/bigline" ffff7777 2>/dev/null | grep -q "session ffff7777-0000-4000-8000-000000000007" \
    || fail "巨大单行: 应按头部 id 定位"
python3 - "${snap%/*}/snap.py" "$work/bigline" <<'PY'
import builtins, os, runpy, sys

api = runpy.run_path(sys.argv[1])
root = sys.argv[2]
real_open = builtins.open

class Counted:
    def __init__(self, fh):
        self.f, self.chars = fh, 0
    def __iter__(self):
        return self
    def __next__(self):
        line = next(self.f)
        self.chars += len(line)
        return line
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return self.f.__exit__(*exc)

read = []
def counting_open(path, *a, **k):
    fh = Counted(real_open(path, *a, **k))
    read.append(fh)
    return fh

builtins.open = counting_open
try:
    found = api["find_session"]("ffff7777", root)
finally:
    builtins.open = real_open

size = os.path.getsize(found)
assert len(read) == 1, f"定位应只读一次头部，实际 {len(read)} 次"
assert read[0].chars <= 4096, f"定位读了 {read[0].chars} 字符(全文 {size}): 头部后的巨大单行被预读了"
print(f"PASS: 头部后巨大单行只读头部({read[0].chars}c / 全文 {size}c)")
PY

printf 'PASS: list / snapshot / --all / --tail / entry view / errors / custom dir\n'
printf 'PASS: run-dir layout / header-id lookup / ambiguity paths / copy cmds\n'

# ── system: 默认和详情均保留原文与工具变更 ─────────────────────────
system_file="$work/system.jsonl"
python3 - "$system_file" <<'PYFIX'
import json, sys
entries = [
    {"type": "session", "id": "system-fixture", "cwd": "/test"},
    {"type": "message", "id": "sys1", "parentId": None, "message": {
        "role": "system", "content": "", "sections": {
            "preamble": "SYSTEM-SECRET正文", "project_context": "项目规则\n第二行"},
        "toolsAdded": [{"name": "notify", "description": "通知定义", "parameters": {"type": "object"}}]}},
    {"type": "message", "id": "sys2", "parentId": "sys1", "message": {
        "role": "system", "content": "直接字符串正文", "toolsRemoved": ["notify"]}},
    {"type": "message", "id": "sys3", "parentId": "sys2", "message": {
        "role": "system", "content": [{"type": "text", "text": "文本块正文"}]}},
]
with open(sys.argv[1], "w", encoding="utf-8") as f:
    for e in entries:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PYFIX
out="$(run "$system_file")"
for expected in '[   2]' '[   3]' '[   4]' 'toolsAdded' 'toolsRemoved' 'SYSTEM-SECRET正文' '直接字符串正文' '文本块正文'; do
    grep -Fq "$expected" <<<"$out" || fail "system: 默认缺 $expected"
done
out="$(run "$system_file" --exclude role:system)"
grep -q 'SYSTEM-SECRET\|toolsAdded\|toolsRemoved' <<<"$out" && fail 'system: 显式排除应删除整个消息子树'
out="$(run "$system_file" sys1)"
for expected in 'preamble' 'SYSTEM-SECRET正文' '项目规则' '第二行' 'toolsAdded' '通知定义' 'parameters'; do
    grep -q "$expected" <<<"$out" || fail "system detail: 缺 $expected"
done
out="$(run "$system_file" sys2)"
grep -q '直接字符串正文' <<<"$out" || fail "system detail: 字符串正文缺失"
grep -q 'toolsRemoved' <<<"$out" || fail "system detail: 移除记录缺失"
out="$(run "$system_file" sys3)"
grep -q '文本块正文' <<<"$out" || fail "system detail: 文本块缺失"
out="$(run "$system_file" sys1 --json)"
grep -q 'sections' <<<"$out" || fail "system json: 原始分区缺失"
printf 'PASS: generic system fields / sections / tool changes / string and block content / raw JSON\n'
python3 "$root/test/summary.py"
python3 "$root/test/fork.py"

python3 "$root/test/dialogue.py"
