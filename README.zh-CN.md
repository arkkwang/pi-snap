[English](README.md) | 简体中文

# pi-snap

为人和 Agent 提供官方 [Pi](https://github.com/earendil-works/pi) 会话的精简执行快照与分叉导出：快速看懂执行过程、定位工具错误、展开原始记录，并从选定记录导出独立会话。不修改原会话，不依赖正在运行的 Pi，不发送模型请求。

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
bash pi-snap <session-id>            # 当前分支的精简执行记录
bash pi-snap <session-id> --all      # 包含回退分支
bash pi-snap <session-id> --tail 20  # 最后 20 条记录
bash pi-snap <session-id> <entry-id> # 完整内容
bash pi-snap <session-id> <entry-id> --json
bash pi-snap --session-dir /path/to/sessions
bash pi-snap /path/to/session.jsonl
```

从其他目录调用时使用 `bash /absolute/path/to/pi-snap/pi-snap ...`。默认读取 `~/.pi/agent/sessions`；支持会话 ID 前缀、文件名前缀或 JSONL 路径，前缀不唯一时会提示。自定义 Pi 数据目录请显式传 `--session-dir`。

快照压缩用户/助手正文、工具调用和结果，隐藏 thinking；单条详情可以展开完整内容。默认只展示最后记录所在的 parentId 分支，`--all` 用 `~` 标记其他分支。工具结果缺失不等于正在执行，也不能证明用户取消；尚未落盘的流式进度无法恢复。

展示规则集中配置在 `snap.py` 顶部：`USER_MAX=800`、`ASST_MAX=600`、默认工具输入 `ARGS_MAX=120`、结果预览 `PEEK_MAX=100`。`TOOL_SUMMARIES` 按工具名配置输入模板、长度和结果摘要，无需增加工具名判断。例如：

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
2. 先看精简快照（大输出可用 `--tail 20`），定位报错工具、异常结束或缺失结果。
3. 用快照中的 entry ID 展开详情；需要保留原始结构时加 `--json`。

快照用于缩小范围，详情用于核对证据。Agent 无需把整份 JSONL 塞进上下文；截断内容和缺失结果都不能作为任务成功或仍在运行的证明。`--json` 当前只用于单条记录，不是全会话 JSON 模式。

## 会话分叉

```bash
pi-snap <session-id> <entry-id> --fork ./retry.jsonl
pi --session ./retry.jsonl
```

`entry-id` 来自快照，也支持唯一前缀。导出沿 `parentId` 保留从根到所选记录的路径，**包含所选记录**；可选被回退的分支，不包含旁支和截止点之后的内容。想重试某条助手回复，选它之前的用户消息或全部工具结果返回后的记录，而不是那条回复本身。

新文件使用新会话 ID、生成时间和指向源文件的 `parentSession`，其余头部字段及路径上的记录原样保留，包括 thinking、图片、系统记录、模型设置、压缩与标签。不重算摘要，不从后续记录补信息。换模型、调整提示词和继续执行交给恢复入口；这里仅导出 JSONL，不恢复工作区、进程或外部服务，也不保证恢复工具会把日志中的系统记录直接用作当前系统提示词。

- 仅支持 v3 会话；目标文件必须不存在，父目录须已存在。成功时 stdout 输出新文件的绝对路径。
- 不允许与 `--all`、`--tail`、`--json` 混用。
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

使用临时合成会话，覆盖分支、截断、错误、前缀定位、重复 ID、路径空格、大输入、分叉完整性、不覆盖源文件及 npm 入口；不读取私人会话或调用模型。

可选 SDK 契约测试：将 `PI_SNAP_SESSION_MANAGER` 设为本机 Pi 的 `dist/core/session-manager.js` 路径，再运行 `npm test`，会用真实 `SessionManager` 打开导出文件，并比较截止点的恢复上下文。未设置时只跑 CLI 回归，不自动安装 Pi。性能复测：`python3 test/benchmark.py`（20,000 条合成记录，预热后 5 次中位数）。

## 相关项目

- [pi-cron](https://github.com/arkkwang/pi-cron)：定时执行 Agent 任务。
- [pi-subagent](https://github.com/arkkwang/pi-subagent)：派发、观察和继续指挥子 Agent。

## License

[MIT](LICENSE)
