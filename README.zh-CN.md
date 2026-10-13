[English](README.md) | 简体中文

# pi-snap

为人和 Agent 提供官方 [Pi](https://github.com/earendil-works/pi) 会话的对话脉络、执行快照与分叉导出：先理解用户目标和讨论转折，再定位工具错误、展开原始记录，并从选定记录导出独立会话。不修改原会话，不依赖正在运行的 Pi，不发送模型请求。

## 使用

需要 Python 3.10+ 和 Bash（Windows 使用 Git Bash；macOS/Linux 使用 Bash）。npm 安装另需 Node.js 22.19+，不会自动安装 Python：

```bash
npm install -g @arkkwang/pi-snap
pi-snap --help
```

确保 `python3` 在 PATH 中；如系统命令名不同，设置 `PI_SNAP_PYTHON` 为 Python 3 可执行文件的路径。npm 命令入口不依赖 Bash；直接使用源码或运行回归测试需要 Bash。

在独立仓库目录运行：

```bash
bash pi-snap --help
bash pi-snap                         # 最近会话
bash pi-snap <session-id>            # 完整对话、工具关键参数与状态
bash pi-snap <session-id> --trace    # 精简执行视图：工具、结果与状态
bash pi-snap <session-id> --all      # 包含回退分支
bash pi-snap <session-id> --tail 20  # 当前视图最后 20 条可显示记录
bash pi-snap <session-id> <entry-id> # 完整内容
bash pi-snap <session-id> <entry-id> --json
bash pi-snap --session-dir /path/to/sessions
bash pi-snap /path/to/session.jsonl
```

从其他目录调用时使用 `bash /absolute/path/to/pi-snap/pi-snap ...`。默认读取 `~/.pi/agent/sessions`；支持会话 ID 前缀、文件名前缀或 JSONL 路径，前缀不唯一时会提示。自定义 Pi 数据目录请显式传 `--session-dir`。

默认就是会话轨迹：完整 user/assistant 正文和换行、工具关键参数（最多透出 100 字符，`TRAJECTORY_ARGS_MAX` 可调，压过各工具更大的限制）、环境摘要。工具结果只在执行失败或状态未知时透出一行状态（ERR/unknown），成功执行整条省略；未产生内容就失败的响应、把记录从模型上下文摘除的 context_edit 属宿主端处理痕迹，不进轨迹——`--trace` 显示这些执行事件，compaction/branch_summary 边界仍保留。不输出 thinking、图片 payload 及**任何工具结果正文**；用户图片保留 `[image]` 占位。未知工具参数优先保留 model/provider/cwd/task，省略常见 payload 字段；这是摘要，不是秘密信息脱敏。仅有工具请求不证明已执行，缺结果不推断正在执行或用户取消；Code Mode 内部调用不从代码或输出猜测。

compaction、branch_summary、context_edit 保持原位置，不展开 summary/replacement 正文。capability-prefetch 自定义消息 `details.prefetch.rounds[].messages` 中真实工具消息使用父记录原 ID，另加 `[recommend]` 标注，原 ID 可直接查父记录详情；建议不算执行。

预取工具使用嵌套消息的实际执行时间（支持毫秒时间戳），另标父记录的 `[落盘 …]`。在完整、一致的时间证据下，仅沿同一分支的祖先链、同一用户/上下文区间前移预取批次，让延后落盘的预取显示在主执行之前；不对普通记录做全局时间排序。时间缺失标“执行时间未知”，时间矛盾或缺少定位依据时保留位置。`--all` 始终保留落盘顺序；`--since/--before` 先按原记录链选范围，再重排范围内预取、过滤和应用 `--tail`，不补入范围外记录。

`--trace` 还显示 `capability-prefetch-diagnostic` 的 `[discovery]` 摘要：输入配置、审查触发/跳过原因与步数、上下文交付、结果和子会话 ID。总配置 `configuredReview` 与局部 `effectiveReview` 分开标示；旧日志缺字段不推断总开关。默认轨迹不展示这些诊断；完整字段用原 ID 加 `--json` 查看。

`--trace` 显式启用已有精简执行摘要，包括有限结果预览、裁短的对话、失败响应与上下文摘除标记；两种展示共用一个消息渲染器。`SESSION ENTRY` 或 `--json` 才展开原记录。默认从文件最后条目沿 parentId 回溯；`--all` 展示其他分支并以 `~` 标记。没有新增全局字符预算或 stdin 模式；调用程序可用 `--tail` 并截取返回字符串，不再重建轨迹语义。

**接线要求：**先发布并安装具有这个新默认行为的 pi-snap，再启用依赖「不透工具结果」的扩展（结果正文一律不透，成功结果连状态行也不透）；0.3.1 及更早版本仍是旧默认。读取时 JSONL 必须已反映当前活动分支，pi-snap 不知道只存在于内存里的分支游标。

## 从脉络展开执行区间

```bash
pi-snap SESSION
pi-snap SESSION --trace --since ID1 --before ID2
pi-snap SESSION ENTRY
pi-snap SESSION ENTRY --json
```

- 所有视图、区间和详情都使用原始记录 ID，不重新编号；支持唯一前缀。不存在、前缀歧义或重复 ID 会报错。
- `--since ID1` 包含起点，`--before ID2` 不包含终点。可以只指定一端；顺序以记录链为准，不比较 ID 字符串或时间。
- 边界可以是工具记录，也可以是对话记录。边界不在当前分支或起点晚于终点时失败；两端相同是空区间，会明确提示。
- 区间暂不与 `--all` 混用，避免悄悄拼接不同分支。
- 顺序为：选分支 → 按原 ID 定位区间 → 投影区间内预取 → 按视图过滤 → `--tail N`。N 必须为正整数；默认无条数限制，指定区间也不隐式截尾。工具摘要只统计保留范围内的调用，丢弃的头部数量会明确显示。
- 同一 Assistant 记录可能同时含正文和多个调用：正文和调用一起显示；`--tail` 按记录数而非输出行数计算，展开的预取消息各占一条。
- 执行摘要的工具清单与调用配对参考完整所选分支，不能因为结果在窗口之外就误报「未记录结果」。

### 执行视图的精简规则

`--trace` 的展示规则集中配置在 `snap.py` 顶部：`USER_MAX=800`、`ASST_MAX=600`、默认工具输入 `ARGS_MAX=120`、结果预览 `PEEK_MAX=100`。`TOOL_SUMMARIES` 按工具名配置输入模板、长度和结果摘要，无需增加工具名判断。例如：

```python
"bash": {"max_chars": 600, "template": "{command}"},
"tool_search": {"max_chars": 400},  # 无模板：保留整个参数 JSON
"example": {
    "template": "{path} · {items:count}",
    "result": {"template": "{status} {lines} lines {size}", "preview_chars": 0},
},
```

- 输入模板按参数名取值；`:size` 显示内容字符数，`:count` 显示条目数。缺失字段为空；可选片段的 `when_any` 要求至少一个参数非 `None`，`defaults` 补缺失或 `None` 值，`0` 仍是有效值。用法见 Read 配置。
- 未配置工具或未配置输入模板时，展示整个参数的单行 JSON；非对象参数保留其内容，不丢成空对象。统一按输入上限截断，保留省略字符数。`truncate="middle"` 保留前 1/3 和后 2/3，适用于 Read/Write/Edit/ls 的路径及后缀；不解析路径，超长文件名仍可能缩短，但保留末尾。默认保留开头。
- 结果模板可用 `{status}`、`{size}`、`{lines}`、`{duration}`（耗时有值时自带前导空格）。默认是状态、大小、耗时；`preview_chars` 控制成功预览，`error_preview_chars` 独立控制错误预览，均缺省为 100，设为 0 不预览。Read 只关闭成功预览；图片标记仍保留。

长度按折叠空白后的字符数计算，不影响原始记录和详情。

System 概览保持一行：`cwd=… · Skills=13 · MCP=5 · Tools=6 · Instructions=1 · Prompt=8.2Kc`。Skills 是可用条目数，MCP 是服务器数，Instructions 是项目指令块数；缺失分区不显示。Tools 沿各自 parentId 分支累计，后续增减显示为 `Tools=8(+2)`，`--tail` 不影响累计。Prompt 是本条记录中各提示词分区与正文的字符数（1Kc=1000 字符），不是 token 数，不含工具定义；增量记录不代表完整上下文大小。

## 人与 Agent 的排查流程

1. 列出最近会话，选定 ID；也可以直接传入日志文件路径。
2. 默认先看对话脉络（大输出可用 `--tail 20`），理解需求、纠正和结论。
3. 用同一组 ID 搭配 `--trace --since ID1 --before ID2` 展开有疑问的一段，核对执行过程。
4. 用 entry ID 展开单条详情；需要保留原始结构时加 `--json`。

对话中的结论和工具请求不是成功证明；执行视图用于缩小范围，详情用于核对证据。Agent 无需把整份 JSONL 塞进上下文；截断内容和缺失结果都不能作为任务成功或仍在运行的证明。`--json` 当前只用于单条记录，不是全会话 JSON 模式。

## 会话分叉

```bash
pi-snap <session-id> <entry-id> --fork ./retry.jsonl
pi --session ./retry.jsonl
```

`entry-id` 来自快照，也支持唯一前缀。导出沿 `parentId` 保留从根到所选记录的路径，**包含所选记录**；可选被回退的分支，不包含旁支和截止点之后的内容。想重试某条助手回复，选它之前的用户消息或全部工具结果返回后的记录，而不是那条回复本身。

新文件使用新会话 ID、生成时间和指向源文件的 `parentSession`，其余头部字段及路径上的记录原样保留，包括 thinking、图片、系统记录、模型设置、压缩与标签。不重算摘要，不从后续记录补信息。换模型、调整提示词和继续执行交给恢复入口；这里仅导出 JSONL，不恢复工作区、进程或外部服务，也不保证恢复工具会把日志中的系统记录直接用作当前系统提示词。

- 仅支持 v3 会话；目标文件必须不存在，父目录须已存在。成功时 stdout 输出新文件的绝对路径。
- 不允许与 `--all`、`--tail`、`--trace`、`--since`、`--before`、`--json` 混用。
- 导出遇到坏行、重复 ID、断裂或循环的祖先链、无效压缩引用、缺少配对的工具结果会失败，不静默补齐或丢弃记录。多个工具调用必须全部返回后再选截止点，或选调用之前的记录。
- 所有内容取自已落盘记录；附件中的外部引用仍由调用方负责。

## 边界

- 这是日志检查与会话分叉工具，不是环境备份、回滚或实时状态服务。
- 打开会话会读取整个文件；`--tail` 限制展示，不限制读取量。
- 查看时无法解析的 JSONL 行会跳过并提示；分叉导出则拒绝坏行。不修复损坏文件。
- 原始会话及输出可能包含提示词、路径、凭据和工具结果。只读不等于脱敏，不要直接公开真实日志。
- 以官方 Pi 会话格式为目标；Windows Git Bash 为当前验证环境，macOS/Linux 尚未实测。

## 测试

```bash
npm ci
npm test
# 仅运行 Python/Bash 回归：
bash test/snap.sh
```

使用临时合成会话，覆盖对话全文、混合正文/工具记录、工具参数与状态、稳定 ID、区间边界、过滤后截尾、分支、截断、错误、前缀定位、重复 ID、路径空格、大输入、分叉完整性、不覆盖源文件及 npm 入口；不读取私人会话或调用模型。

可选 SDK 契约测试：将 `PI_SNAP_SESSION_MANAGER` 设为本机 Pi 的 `dist/core/session-manager.js` 路径，再运行 `npm test`，会用真实 `SessionManager` 打开导出文件，并比较截止点的恢复上下文。未设置时只跑 CLI 回归，不自动安装 Pi。性能复测：`python3 test/benchmark.py`（20,000 条合成记录，预热后 5 次中位数）。

## 相关项目

- [pi-cron](https://github.com/arkkwang/pi-cron)：定时执行 Agent 任务。
- [pi-subagent](https://github.com/arkkwang/pi-subagent)：派发、观察和继续指挥子 Agent。

## License

[MIT](LICENSE)
