# Transparent Agent

一个本地的轻量终端编码 agent：理解任务、检查项目、读改文件、执行命令、根据结果迭代、验证后汇报。

本项目最终目标是 **Transparent Agent**（agent 的每一步都可被外部检查），但当前版本 **只实现 agent 本身**。
透明层尚未实现，架构上只保留了接入点：agent core 通过事件（events）向外通知，从不依赖任何观察者存在。
把未来的透明组件全部删掉，agent 的行为和 TUI 不受任何影响。

## 当前状态

已实现（V1）：

- 原生 tool calling 的迭代 agent loop（模型自主决定调用哪些工具、调用多少次，不预设流程）；
- 七个工具：`read` `write` `edit` `ls` `find` `grep` `bash`；
- 流式输出、持久多轮会话、Ctrl+C 中断、常用斜杠命令的轻量 TUI；
- OpenAI 兼容的模型适配层（`chat/completions` + 流式 + 工具调用），换 provider 只需新增一个 adapter；
- 可选事件钩子（sink），默认无观察者时行为完全一致；
- 工具输出截断、bash 超时/进程组清理、工作区路径越界防护。

未实现（有意留到后续版本）：透明/检查界面、多 agent、子 agent、计划器、todo、长期记忆、RAG、MCP、
浏览器自动化、上下文压缩、检查点回滚、跨进程会话恢复、Web UI、基准框架。

## 安装与运行

依赖：Python ≥ 3.10，`openai` SDK（≥1.0）。grep 会优先使用 `rg`，没有 `ripgrep` 时自动退回 Python 实现。

```bash
pip install -e .                      # 可选：装好 console script（需要较新的 pip/setuptools）
python -m transparent_agent           # 在当前目录启动（未安装时在仓库根目录运行）
transparent-agent -C ~/my-project     # 指定工作区
transparent-agent -p "fix the failing tests" -C ~/my-project   # 单次非交互
```

## 配置

把 key 写进用户级配置文件后，在任意目录直接 `transparent-agent` 即可：

```ini
# ~/.config/transparent-agent/.env
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-flash
OPENAI_API_KEY=sk-...
```

优先级：CLI 参数 → 环境变量 → 当前目录 `.env` → `~/.config/transparent-agent/.env`。
已在环境中的变量不会被 `.env` 覆盖；`.env` 支持 `export` 前缀、引号和 `#` 注释，
上面配置表里的任何变量都可以写进去（例如 `MAX_STEPS=60`）。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 无（必填） | API key |
| `OPENAI_BASE_URL` | OpenAI 官方 | 任意 OpenAI 兼容端点，例如 `https://api.deepseek.com` |
| `OPENAI_MODEL` | `gpt-4o` | 支持 function calling 的模型 |
| `MAX_STEPS` | `40` | 每个用户回合允许的模型请求次数上限 |
| `BASH_TIMEOUT_S` / `MAX_BASH_TIMEOUT_S` | `120` / `600` | bash 默认/最大超时 |
| `MAX_TOOL_OUTPUT_CHARS` | `30000` | 单次工具结果回填给模型的最大长度（超出中间截断并注明） |
| `MODEL_TIMEOUT_S` | `300` | 单次模型请求超时 |
| `STREAM_USAGE` | `true` | 是否向端点请求流式 usage；端点不支持时设为 `false` |
| `WORKSPACE_DIR` | 当前目录 | 默认工作区 |

## 交互

```text
$ python -m transparent_agent -C ~/project
workspace: /home/you/project
model: deepseek-chat  (https://api.deepseek.com)

> Find why the parser tests fail and fix them.

● Inspecting the project
  → ls .
  → read tests/test_parser.py
  → grep /parse_config/ in src

● The failure is in parse_config.
  → edit src/parser.py

● Running the tests.
  → bash pytest -q
  8 passed
✓ done (5 steps, 31.2k tokens)

>
```

- Enter 发送；行尾 `\` 续行。
- `Ctrl+C` 中断当前生成或命令（命令的整个进程组会被终止），会话继续可用；提示符处 `Ctrl+C` 取消当前输入。
- `Ctrl+D` 或 `/exit` 退出。
- 命令：`/help` `/clear` `/status` `/model [NAME]` `/exit`。
- `-p/--print` 非交互模式：助手文本到 stdout，工具活动到 stderr，便于脚本化。

## 架构

```text
TUI（tui/）        ← 只消费事件做展示，agent 不感知
     │  events（可选 sink）
Agent Core（agent.py）  ← 迭代循环、会话历史、工具分派
     │
Model Interface（models.py） → Provider（providers/openai_chat.py）
     │
Tools / Environment（tools/：base fs search shell）
```

| 模块 | 职责 |
| --- | --- |
| `agent.py` | 迭代循环：构造请求 → 流式接收 → 执行工具 → 回填结果 → 再次请求；终止、取消、步数上限、历史一致性 |
| `models.py` | 归一化类型（Message/ToolCall/ModelResponse/Usage）与 Model 协议 |
| `providers/openai_chat.py` | OpenAI 兼容适配器：请求编码、SSE 增量重组（工具调用按 index 拼接）、usage、错误映射 |
| `tools/` | 工具实现、注册表与执行器（预期失败转成给模型的观测结果）、工作区路径防护 |
| `events.py` | 事件类型与 sink 协议（`NullSink` 默认，`CompositeSink` 组合） |
| `tui/` | 渲染（事件 → 终端输出）与 REPL（输入、命令、中断） |

设计原则：

- **agent core 不依赖任何接口层**：不打印、不知道 TUI 存在；`NullSink` 下行为完全一致。
- **模型抽象**：core 只看到归一化消息/工具/流式事件；provider 细节留在 adapter 内。
- **工具失败是观测**：不存在的文件、patch 不匹配、命令非零退出、超时都是结构化结果回填给模型，不终止程序。
- **事件是通知，不是控制流**：sink 抛异常不会影响 agent；事件 payload 是纯 JSON 可序列化数据。

### 事件

`agent_start` · `user_message` · `model_request_start`（完整 system + messages + tools 快照）·
`model_text_delta` · `model_response`（文本、工具调用、finish_reason、usage）·
`tool_call_start` / `tool_call_end`（call_id、参数、输出、is_error、耗时、是否取消）·
`agent_error` · `agent_finish`（stop_reason、步数、usage、耗时）。

外部观察者接入示例见 `examples/event_logger.py`（把每个事件写成 JSONL，与 TUI 同时挂载）。

## 工具

| 工具 | 说明 |
| --- | --- |
| `read` | 读文本文件，带行号；支持 `start_line`/`end_line`；大文件只返回前 2000 行并注明；二进制文件明确报错 |
| `write` | 创建/整体覆盖文件，自动建父目录 |
| `edit` | 精确字符串替换（`old_string`→`new_string`）；要求唯一或 `replace_all`；失败时说明原因；返回小 diff |
| `ls` | 列目录（非递归，最多 200 条） |
| `find` | 按文件名 glob 递归查找，可限定 file/dir |
| `grep` | 正则搜索内容，返回 `path:line:text`；优先 `rg`，无则 Python 回退；支持 `glob`/`ignore_case` |
| `bash` | 在工作区执行命令；返回 stdout/stderr/退出码/耗时/超时状态；进程组超时或取消时整体终止 |

约束：所有路径解析后必须位于工作区内（含符号链接解析），逃逸请求作为错误返回给模型；
bash 默认 cwd 为工作区，单次命令内的 `cd`/环境变量不影响后续调用；输出超限时中间截断并注明。

## 测试

```bash
python3 -m pytest -q          # 131 个单元/集成测试，使用脚本化的假模型，不调用真实 API
```

端到端验收（需要真实模型与 key）：

```bash
OPENAI_API_KEY=... OPENAI_BASE_URL=... OPENAI_MODEL=... python3 scripts/e2e.py
python3 scripts/e2e.py --only B,F --keep
```

8 个场景：A 仓库理解（含无 sink 对照）、B 单文件修 bug、C 多文件改动、D 新建文件、E 失败恢复、
F 同一会话追问、G 真实 CLI 冒烟。每次运行都会新建临时工作区、真实执行，结束后检查磁盘结果与事件记录；
过程记录写入 `.e2e/<场景>.txt`。

## 已知限制

- 未安装 `ripgrep` 时 grep 的回退实现不解析 `.gitignore`（只跳过常见目录）。
- bash 继承当前环境变量（API key 会被子进程看到）；没有沙箱、没有命令审批。
- 上下文只做固定阈值截断，没有压缩/摘要；超长会话最终会撞到模型上下文上限。
- 会话只在进程内存在，重启不恢复。
- 单轮内多个工具调用串行执行。
- 只实现了 `chat/completions` 一种协议（OpenAI 兼容）；Anthropic 等其它协议需要新 adapter。
- 推理型模型返回的 reasoning 文本会被保留并在后续请求中回传，但不在 TUI 展示。
