# 扩展边界与演进

下表覆盖完整 agent 的能力边界，不代表当前内置。新增先补可观察契约和验收。

| 能力 | 归属 | 额外记录 |
| --- | --- | --- |
| 多 provider/多模态 | Model adapter | 原始协议、媒体引用、归一化损失 |
| 自动压缩 | ContextPolicy+Model | 前后、摘要请求、原文范围 |
| steering/follow-up | runtime 调度 | 入队/消费/生效 step |
| 树形 session | Store | parent/head/分支 |
| checkpoint/rollback | 文件版本组件 | hash、恢复结果 |
| MCP | Tool adapter | server、连接/调用、未知字段 |
| sub/multi-agent | orchestration extension | parent_call_id、子 trace、usage |
| 长期记忆/RAG | 显式工具 | 查询/来源/选取/写入 |
| planner/TODO | 文件/工具 | 计划更新、执行偏差 |
| 权限/沙箱 | OS/policy | 决策、隔离配置 |
| Web（已实现）/通用 RPC（待扩展） | interface adapter | 输入输出、session 关联；见 [Web](web-ui.md) |
| package/theme | 资源/展示 | 来源、版本、加载 |

不做隐藏后台请求。嵌套模型复用带 trace provider，嵌套工具走 executor 并关联父调用。同进程扩展可违背约定，不视作沙箱。
