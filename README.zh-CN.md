[English](README.md) | 简体中文

# pi-snap

面向人与 Agent 的 **Pi 会话记录读取、筛选与展示工具**：用紧凑单行查看对话和执行路径，按序号定位完整证据，也支持在指定记录处导出独立会话。

底层保留原始 JSON，新增类型、内容块和字段有通用展示，不必逐项适配。筛选决定看什么，统一渲染决定怎么显示；视图只是参数组合。不调用模型，不修改源会话。

```text
[   2] 12:05:23 [type:message > role:user > type:text] 检查这个文件
[ 128] 12:05:25 [type:message > role:assistant > type:toolCall] → read path=/project/file.py, offset=10
[ 132] 12:05:26 [type:message > role:assistant > type:text] 检查完成。
```

## 安装与使用

需要 Python 3.10+；npm 入口还需要 Node.js 22.19+，不会自动安装 Python。

```bash
npm install -g @arkkwang/pi-snap
pi-snap                              # 列出最近会话
pi-snap --list-views                 # 查看两种视图及默认筛选
pi-snap SESSION --view story         # 通读对话主线，正文完整不截断
pi-snap SESSION --view trace         # 对话、调用发起、行为推荐
pi-snap SESSION --list-types         # 实际结构标签路径与可复制选择器
pi-snap SESSION                      # 完整展示（不指定视图）
pi-snap SESSION --only user,assistant --exclude thinking,image
pi-snap SESSION --view trace --max-chars 500 --tail 20
pi-snap SESSION 128                  # 完整单条记录
pi-snap SESSION 128 --json           # 原始 JSON
```

`SESSION` 支持会话 ID 前缀、文件名前缀或 JSONL 路径。默认目录为 `~/.pi/agent/sessions`，可用 `--session-dir PATH` 指定。源码运行用 `python3 snap.py` 或 `bash pi-snap`；npm 入口可通过 `PI_SNAP_PYTHON` 指定 Python。

## 展示与编号

- 按完整文件中的有效记录从 1 编号（含会话头），先编号后筛选，不随分支、区间或 `--tail` 重编号。追加记录不改变已有编号；修改旧文件可能改变编号。
- 序号右对齐、左补空格，至少 4 位；超过 9999 条时整列统一扩宽。原始 ID 留在详情，不重复占据列表。
- 每行格式为 `[序号] 时间 [标签链] 正文`。标签链是带字段限定的真实来源嵌套，例如 `type:message > role:assistant > type:toolCall`；同节点多个标签均保留（`type:custom_message+customType:capability-prefetch`）。工具名在正文中：`→ read …` 表示调用发起，`← read …` 表示结果。嵌套准备消息的行保留宿主记录标签，不伪装成真实顶层 assistant；无任何标签时回退为 `record`。
- 同一记录内的正文、内容块或调用分别成行，共用序号。被过滤的记录直接消失，不插入省略提示。
- 正文折成一行，默认每行最多 300 字符；`--max-chars N` 调整，`0` 不限。截断只在行末标明省略量，不截断定位列。
- usage、签名等已知元数据放入详情；新类型、新内容块和未识别字段以紧凑的 `字段=值` 展示。不维护逐工具摘要模板。
- 默认本地时间 `HH:mm:ss`，日期放会话头，跨天时补日期，不显示“时间”标签或毫秒。内部排序和时间筛选仍使用完整精度。

## 视图与筛选

`--view story|trace` 选择视图；不指定 `--view` 时完整展示。视图只是参数组合——选择器、筛选和渲染器与其它入口完全共用：

| 视图 | 保留 | 隐藏 | 正文长度 |
| --- | --- | --- | --- |
| `story` | 真正的用户与 assistant 正文（含调用前说明） | thinking、工具调用/结果、图片、能力推荐（`capability-prefetch`）、被筛到没有正文的空壳记录 | `0`（不限） |
| `trace` | 对话、调用发起、行为推荐与提醒 | thinking、工具结果、图片数据 | `300` |

- `story` 保留中途用户纠正，不生成摘要。空壳抑制属于该视图本身：内容块被全部滤掉的消息整条消失，不会留下 `content=[]` 行；不带 `--view` 时自定义 `--only`/`--exclude` 行为不变，仍会显示空壳行。
- 显式 `--only` 替换视图包含列表，`--exclude` 追加到视图排除项（排除优先），`--max-chars` 覆盖视图长度默认值。
- `pi-snap --list-views`（无需 session）展示各视图用途、隐藏内容及选择器默认值；视图定义单点维护在 `VIEWS` 配置中。

`--only TYPES` 包含匹配对象及其子树；`--exclude TYPES` 排除匹配对象及其子树，排除优先。参数支持逗号分隔和重复传入，递归精确匹配字符串 `type`、`role`、`customType`。可加字段限定，例如 `--only role:assistant`、`--only type:新类型`；值中含冒号时须加限定。

混合消息可只排除 thinking 而保留正文和调用。仅选后代时保留来源和祖先路径，不带入无关兄弟内容。匹配是结构筛选，不代表执行证明，也不等于脱敏。

## 类型发现

`pi-snap SESSION --list-types` 列出实际出现的结构路径，每行附可直接复制的限定选择器：

```text
$.message.role                                 role:user
$.message.content[].type                       type:toolCall
$.details.prefetch.rounds[].messages[].role    role:toolResult
```

数组位置合并为 `[]`；同一路径+选择器只列一次。发现范围默认当前分支（可 `--all`），支持 `--since`/`--before`/`--since-time`/`--before-time`，且先于任何视图与筛选——视图隐藏的类型仍可发现。它与其它参数（视图、筛选、长度、`--tail`、单条、`--json`、`--fork`）组合时明确报错。发现只读结构：不递归解析字符串里的 JSON，不把日志正文当事件；未知插件字段照列不猜含义。

## 定位与时间

```bash
pi-snap SESSION --since 128 --before 160
pi-snap SESSION --since-time 2026-10-13T08:00:00+08:00 --before-time 2026-10-13T08:05:00+08:00
pi-snap SESSION --all
```

- 详情、区间和分叉都可使用序号或原始 ID／唯一前缀。整数优先解释为序号；纯数字原始 ID 用 `id:0532` 消歧。
- 默认选择文件最后一条记录的 `parentId` 分支并包含会话头。`--all` 保留文件顺序，旁支标记 `~`，不能与记录区间混用。
- 区间包含起点、不含终点。记录区间先于重排和筛选确定；时间参数须带时区。无效、歧义、重复、旁支或倒序边界会报错。
- `--tail N` 在筛选后取最后 N 条原始记录，不是输出行数。单条详情保留全文和全部字段，不接受筛选／长度参数；`--json` 仅用于单条记录。
- 普通消息优先用消息自身时间，字段缺省时用 Pi 记录时间。预取使用 `details.prefetch.rounds[].messages` 中真实调用／结果的执行时间，不以外层落盘时间替代；完整精度和落盘时间可在详情查看。
- 延迟预取仅在时间完整、一致且有已知祖先下界时安全前移，不跨用户／系统、上下文或记录区间边界；因此序号可能局部不递增。`--all` 不重排。时间段命中批次中任一事件时保留整条源记录，未知时间不匹配并提示，冲突不猜顺序。

## 分叉导出

```bash
pi-snap SESSION 128 --fork ./retry.jsonl
pi --session ./retry.jsonl
```

沿 `parentId` 导出到所选记录（包含该条），也可选择旁支。仅支持 Pi v3，目标须不存在、父目录须已存在。新建会话 ID、时间和 `parentSession`，其余字段原样保留；不恢复文件、进程或外部服务。

导出不能混用视图、筛选、区间、长度或 `--json`。坏行、无效／重复 ID、断裂或循环祖先链、无效压缩引用、未配对工具结果会导致失败。截止点应在一批工具调用之前，或全部结果返回之后。

## 注意与测试

- 默认不按类型隐藏思考、工具结果或图片内容。旧 `--trace` 与 `--preset` 已移除，未知参数按 argparse 标准报错；改用 `--view` 或显式筛选。
- 读取整个文件后筛选；`--tail` 不减少读取量，单行长度限制不是总输出上限。查看时坏行警告并跳过，导出则拒绝。
- 通用展示不等于自动理解未来的分支、时间或恢复协议。日志可能含凭据，未落盘内容不可恢复，缺失结果不能证明执行或取消。

运行 `npm test` 验证合成会话，`python3 test/benchmark.py` 复测性能。可选真实 Pi SDK 验证：设置 `PI_SNAP_SESSION_MANAGER` 为已安装 Pi 的 `dist/core/session-manager.js`。本地验证环境为 Windows Git Bash。

[MIT License](LICENSE)
