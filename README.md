# Transparent Agent

参照 pi 分层思想实现的本地 Python 编码 agent。**每次模型请求、API 原始返回、工具参数与结果都可以检查**。默认 CLI 保存 JSONL trace 和可恢复会话，保留轻量 TUI。

从 [文档库](docs/README.md) 开始阅读完整设计；代码对照与验收见 [实现状态](docs/implementation.md)。本轮先独立提交文档，再按契约重构。

## 安装与运行

Python ≥ 3.10。shell 与进程组清理针对 Linux/macOS，Windows 推荐 WSL。grep 优先 rg，没有时 Python 回退。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
python -m transparent_agent -C ~/my-project
transparent-agent -C ~/my-project -p '修复失败测试并验证'
```

模型配置写入 `~/.config/transparent-agent/.env` 或启动目录 `.env`：

```ini
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=your-tool-capable-model
```

优先级 CLI → 环境 → 启动目录 .env → 用户配置 → 默认。支持原生 function calling 的 OpenAI 兼容 Chat Completions，其它协议需要适配器。

使用独立虚拟环境和较新 pip，避免旧系统构建工具忽略 pyproject.toml 元数据。构建 backend 要求 setuptools ≥ 68。

## 检查与恢复

启动显示 trace.jsonl 和 session.json 的绝对路径，默认在 `~/.local/state/transparent-agent/sessions/<id>/`。`--state-dir` 改状态根，`--trace-dir` 改新会话 trace 根，`/trace` 显示当前路径。

```bash
transparent-agent --inspect /absolute/path/trace.jsonl
transparent-agent --resume /absolute/path/session.json
transparent-agent --resume /absolute/path/session.json -p '继续验证'
```

inspect 离线显示完整事件，无需 key，不执行工具。resume 恢复历史并追加原 trace，不重放工具；悬空调用补「状态未知」错误。一个 session 只允许一个活动进程持锁。

记录包括：

- 完整 system、历史、schema、归一化响应和 API 公开的 reasoning。
- 实际 HTTP method/URL/headers/body，以及响应状态、headers、原始字节与未知字段；字节以 base64 保存。
- 工具原始参数、完整结果、截断回填结果、metadata、失败/取消和耗时；shell 双流也保存原始字节。
- 上下文和资源来源、session/run/request/call 关联、严格递增序号。

认证 header/URL 参数脱敏，任务正文和源码原样保存。记录本地存储、Git 忽略、不会自动上传。trace/session 写失败停止运行，普通观察者失败不阻断。详见 [透明记录](docs/transparency.md)。

## Skills、上下文与模板

读取项目根到 workspace 的 AGENTS.md。skill 从 `.agents/skills/<name>/SKILL.md`、`~/.config/transparent-agent/skills`、`~/.agents/skills` 发现；`--skill-dir` 可重复增加目录。

```markdown
---
name: test-fix
description: Diagnose and fix failing tests. Use when a test suite fails.
---
读取失败测试和相关实现，复现问题，针对性修复，再运行相关测试。
```

system 只包含目录元数据，模型用 load_skill 按需读正文/相对资源；用户用 `/skill:test-fix <任务>` 显式加载。disable-model-invocation=true 限制为显式命令。优先级为显式目录、项目、用户，同名冲突和坏资源有诊断。

模板在 `.agents/prompts/<name>.md` 或用户配置 prompts，通过 `/prompt:<name> <参数>` 展开 `$ARGUMENTS`。只替换文字，不执行 shell。见 [Skills](docs/skills.md)、[上下文](docs/context.md)、[模板](docs/prompts.md)、[上手示例](docs/tutorial.md)。

## 工具与扩展

| 工具 | 行为 |
| --- | --- |
| read | 带行号、支持范围，默认前 2000 行 |
| write | 创建/完整覆盖 |
| edit | 唯一精确替换，或显式 replace_all |
| ls | 当前层目录，最多 200 项 |
| find / grep | glob 路径 / 正则内容查询 |
| bash | shell、双流、退出码、超时、进程组清理 |
| load_skill | 发现 skill 时加入，受控按需读取 |

JSON Schema 校验在执行前；坏参数不产生副作用。文件/skill 路径 resolve 后不得越界。工具串行，失败作为错误观测供模型纠正。模型输出截断，trace 保留本次返回全文；分页、匹配数量和扫描范围限制分别说明。

--extension file.py 显式导入 Python 扩展，register(api) 注册工具/观察者；有当前进程权限。详见 [工具](docs/tools/README.md)、[扩展](docs/extensions.md)。

## 交互与配置

保留 Enter、反斜杠续行、Ctrl+C、Ctrl+D、流式渲染和 `/help`、`/clear`、`/status`、`/model [NAME]`、`/exit`。新增 `/skills`、`/skill:NAME`、`/prompt:NAME`、`/trace`。print 模式助手文字到 stdout，工具/路径到 stderr。

| 配置 | 默认 |
| --- | --- |
| MAX_STEPS | 40 模型请求/提交 |
| MODEL_TIMEOUT_S | 300 秒 |
| BASH_TIMEOUT_S / MAX_BASH_TIMEOUT_S | 120 / 600 秒 |
| MAX_TOOL_OUTPUT_CHARS | 30000 字符 |
| STREAM_USAGE | true |
| WORKSPACE_DIR | 当前目录 |
| TRANSPARENT_AGENT_STATE_DIR | ~/.local/state/transparent-agent |

数字必须正且有限；shell 参数超过最大超时时注明并钳制。见 [配置](docs/configuration.md)。

## 架构与验证

runtime.py 组装资源、扩展、存储、provider 和 agent；loop 保持有限反馈循环；provider transport 记录实际 body；TUI 只展示事件。SDK 可直接嵌入 Agent，或用 Runtime 上下文管理器获得默认持久记录。

```bash
python3 -m pytest -q
python3 -m transparent_agent --help
```

测试覆盖 loop/TUI、HTTP 字节、长输出、取消、资源、会话/锁和两个真实 CLI 进程的恢复，使用本地 fake HTTP，不消耗真实 API。真实模型验收另用 scripts/e2e.py，需要有效配置。

## 当前边界

没有自动摘要、树形会话、回滚、内置 MCP/子 agent/长期记忆或 Web/RPC；位置见 [演进](docs/evolution.md)。长对话可能超 context；查询有范围/条数限制；完整 shell 输出占内存/磁盘。bash 继承环境且无 OS 沙箱，需要隔离时使用容器/系统机制。TUI 展示摘要，完整检查通过 trace/inspect。
