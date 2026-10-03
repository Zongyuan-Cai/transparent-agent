# Transparent Agent 文档库

本库先定义一个独立、完整、可以检查每一步的编码 agent，再指导实现。「必须」表示运行时契约，可选能力不代表已经实现。当前使用 TypeScript；迁移范围见 [迁移契约](typescript-migration.md)，结果见 [实现状态](implementation.md)，使用见 [上手示例](tutorial.md)。

## 阅读路径

1. [原则](principles.md) → [架构](architecture.md) → [生命周期](lifecycle.md)。
2. [数据模型](messages.md) → [Provider](providers.md) → [工具协议](tools/README.md)。
3. [上下文](context.md) → [提示词与模板](prompts.md) → [Skills](skills.md) → [扩展](extensions.md)。
4. [事件](events.md) → [透明记录](transparency.md) → [会话](sessions.md)。
5. [配置](configuration.md) → [接口与 TUI](interfaces.md) → [独立 Web UI](web-ui.md) → [错误](errors.md) → [环境边界](security.md) → [验证](testing.md)。

## 组件索引

| 组件 | 输入 → 输出 | 责任 |
| --- | --- | --- |
| Agent | 用户消息 → 回复、停止原因 | 有限的模型/工具反馈循环 |
| Model / Provider | 历史、schema → 流、响应 | 协议适配、原始传输记录 |
| Registry / Executor | 工具名、JSON 参数 → ToolResult | 注册、校验、执行、取消 |
| [文件工具](tools/filesystem.md) | 路径、文本 → 内容、diff | read / write / edit / ls |
| [搜索](tools/search.md) | glob、正则 → 匹配 | find / grep |
| [命令](tools/shell.md) | command、cwd、timeout → 输出 | bash、进程组清理 |
| SkillCatalog | 资源目录 → 目录/正文 | 按需加载专业说明 |
| ContextBuilder | 基础提示、AGENTS.md、资源 → system | 可溯源组装 |
| Template | 名称、参数 → 用户消息 | 显式文本复用 |
| Extension | 显式 ESM 模块 → 工具/观察者 | 扩展执行能力 |
| EventBus | 运行事实 → 通知 | UI 与记录解耦 |
| TraceRecorder | HTTP/事件/结果 → JSONL | 默认持久透明记录 |
| SessionStore | 完成历史 ↔ 快照 | 跨进程恢复 |
| CLI / SDK / TUI / [Web](web-ui.md) | 输入 → runtime | 独立入口、组装和展示 |

完整 agent 必须有输入、上下文、模型、工具、反馈、停止/取消、会话、skills、可观察接口和可验证错误处理。MCP、子 agent、长期记忆、自动摘要、图像等能力的位置见 [演进](evolution.md)；取舍见 [决策](decisions.md)，依据见 [参考](references.md)。
