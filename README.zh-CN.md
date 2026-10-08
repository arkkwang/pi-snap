[English](README.md) | 简体中文

# pi-snap

为人和 Agent 提供精简执行快照，只读检查官方 [Pi](https://github.com/earendil-works/pi) 的会话记录：快速看懂执行过程、定位工具错误，并展开原始记录。不依赖正在运行的 Pi，不发送模型请求。

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

## 人与 Agent 的排查流程

1. 列出最近会话，选定 ID；也可以直接传入日志文件路径。
2. 先看精简快照（大输出可用 `--tail 20`），定位报错工具、异常结束或缺失结果。
3. 用快照中的 entry ID 展开详情；需要保留原始结构时加 `--json`。

快照用于缩小范围，详情用于核对证据。Agent 无需把整份 JSONL 塞进上下文；截断内容和缺失结果都不能作为任务成功或仍在运行的证明。`--json` 当前只用于单条记录，不是全会话 JSON 模式。

## 边界

- 这是日志检查工具，不是备份、恢复或实时状态服务。
- 打开会话会读取整个文件；`--tail` 限制展示，不限制读取量。
- 无法解析的 JSONL 行会跳过并提示，适用于正在写入的半行，但不修复损坏文件。
- 原始会话及输出可能包含提示词、路径、凭据和工具结果。只读不等于脱敏，不要直接公开真实日志。
- 以官方 Pi 会话格式为目标；Windows Git Bash 为当前验证环境，macOS/Linux 尚未实测。

## 测试

```bash
npm ci
npm test
# 仅运行 Python/Bash 回归：
bash test/snap.sh
```

使用临时合成会话，覆盖分支、截断、错误、前缀定位、重复 ID、路径空格、大输入及 npm 入口；不读取私人会话或调用模型。

## 相关项目

- [pi-cron](https://github.com/arkkwang/pi-cron)：定时执行 Agent 任务。
- [pi-subagent](https://github.com/arkkwang/pi-subagent)：派发、观察和继续指挥子 Agent。

## License

[MIT](LICENSE)
