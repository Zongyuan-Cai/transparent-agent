# Transparent Agent

参照 pi 分层思想实现的本地 **TypeScript 编码 agent**。每次模型请求、API 原始返回、工具参数与结果都可以检查。终端和独立 Web UI 共用 runtime、JSONL trace 和可恢复会话。

完整组件设计见 [文档库](docs/README.md)，本次替换范围见 [TypeScript 迁移](docs/typescript-migration.md)，代码与验收见 [实现状态](docs/implementation.md)。

## 安装与运行

Node.js ≥ 24，Linux/macOS；Windows 使用 WSL。源码采用严格 TypeScript / ESM，发布包提供编译后的 JavaScript 和类型声明。

```bash
npm ci
npm run build
npm start -- -C ~/my-project
npm start -- -C ~/my-project -p '修复失败测试并验证'
```

POSIX 会话锁使用 `fs-ext` 原生绑定，需要 C++ 构建工具和 node-gyp 构建环境（Debian/Ubuntu 通常为 `build-essential`、`python3`）。这些是安装时工具，agent 运行不调用 Python。较新 npm 默认阻止未授权依赖脚本；仓库已将 `fs-ext@2.1.1` 写入 `allowScripts`。若安装时仍提示阻止，执行 `npm approve-scripts fs-ext`、`npm rebuild fs-ext`。

模型配置写入 `~/.config/transparent-agent/.env` 或启动目录 `.env`：

```ini
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=your-tool-capable-model
```

优先级 CLI → 环境 → 启动目录 .env → 用户配置 → 默认。支持原生 function calling 的 OpenAI 兼容 Chat Completions；其他协议通过 `Model` 接口适配。

## 独立 Web UI

```bash
npm run web -- -C ~/my-project --port 8765
```

打开 [本地 Web UI](http://127.0.0.1:8765)。后端直接使用 Runtime，不导入 TUI；静态 HTML/CSS/JS 随 npm 包发布，无前端构建或 CDN 依赖。API key 留在服务端环境。

- 会话列表：创建、选择和恢复工作区会话。
- 对话区：流式文字、公开推理文本、工具卡片、停止任务、Skills/模板和清空对话。
- 检查区：筛选、搜索和分页；展开完整请求 body、原始响应、工具全文与 JSON；下载 trace。

每个活动会话由独立 Node 子进程运行。浏览器断开不停止任务，同一会话与 TUI 互斥。默认只绑定本机。接口与边界见 [Web UI 文档](docs/web-ui.md)。

无真实 API 的演示使用临时工作区和本地模拟 HTTP 服务：

```bash
npm run demo -- --port 8766
```

## 检查与恢复

启动显示 trace.jsonl 和 session.json 的绝对路径，默认在 `~/.local/state/transparent-agent/sessions/<id>/`。`--state-dir` 改状态根；`--trace-dir` 改新会话 trace 根。

```bash
node bin/agent.mjs --inspect /absolute/path/trace.jsonl
node bin/agent.mjs --resume /absolute/path/session.json
node bin/agent.mjs --resume /absolute/path/session.json -p '继续验证'
```

inspect 无需 key，不执行模型或工具。resume 恢复历史并追加 trace，不重放工具；悬空调用补「状态未知」错误。Python v1 的 session/trace 保持兼容，旧 `.py` 扩展需要迁移到 ESM。

记录包括完整 system、历史、schema、归一化响应；实际 HTTP method/URL/headers/body、原始压缩字节和未知返回字段；工具原始参数、完整结果、模型所见截断结果、失败/取消与耗时。API 公开的 reasoning 也保留。

认证 headers/URL 参数脱敏，任务正文和源码原样保存。trace/session 存储为用户私有文件，不自动上传。记录写失败停止运行。详见 [透明记录](docs/transparency.md)。

## Skills、上下文与扩展

读取项目根到 workspace 的 AGENTS.md。Skill 从 `.agents/skills`、`~/.config/transparent-agent/skills`、`~/.agents/skills` 发现；`--skill-dir` 可重复添加目录。

```markdown
---
name: test-fix
description: Diagnose and fix failing tests. Use when a test suite fails.
---
读取失败测试和相关实现，复现问题，针对性修复，再运行相关测试。
```

system 只包含元数据；模型用 `load_skill` 按需读正文/相对资源。用户用 `/skill:test-fix <任务>` 显式加载；`disable-model-invocation: true` 限制为显式命令。

模板 `.agents/prompts/<name>.md` 用 `/prompt:<name> <参数>` 展开 `$ARGUMENTS`，只替换文字。`--extension file.mjs` 或 `file.ts` 显式加载 ESM `register(api)`，注册工具/观察者。见 [Skills](docs/skills.md)、[上下文](docs/context.md)、[模板](docs/prompts.md)、[扩展](docs/extensions.md) 和 [SDK 示例](docs/tutorial.md)。

| 工具 | 行为 |
| --- | --- |
| read | 带行号、支持范围，默认前 2000 行 |
| write / edit | 完整写入 / 唯一精确替换或显式 replace_all |
| ls | 当前层目录，最多 200 项 |
| find / grep | glob 路径 / 可取消 worker 中的 JavaScript 正则 |
| bash | shell、双流、退出码、超时与进程组清理 |

终端保留 Enter、反斜杠续行、Ctrl+C、Ctrl+D、流式渲染，以及 `/help`、`/clear`、`/status`、`/model`、`/skills`、`/trace`、`/exit`。

## 开发与发布包验证

```bash
npm run build
npm run typecheck
npm test
npm run check:docs
npm pack
node scripts/check-install.ts ./transparent-agent-0.2.0.tgz
```

测试使用本地 HTTP 模型与真实工具/进程，不消耗真实 API。可选真实模型验收运行 `node scripts/e2e.ts`，需有效配置。包安装后提供 `transparent-agent` 和 `transparent-agent-web` 命令。

目前不内置 MCP、子 agent、自动摘要、长期记忆或公网账号服务。shell 和扩展具有本地用户权限；文件边界不等于操作系统沙箱。完整输出暂存内存，无硬配额。详见 [环境边界](docs/security.md) 和 [演进](docs/evolution.md)。
