# 实现状态

初始组件文档先于 Python 重构完成；本分支先写 [迁移契约](typescript-migration.md)，再将完整运行时迁移到严格 TypeScript。Python 旧实现位于提交 `378925b8f6633fe08272ed0567c38b28cee0a086`。

| 组件 | 实现位置 | 行为 |
| --- | --- | --- |
| Loop / 类型 | src/agent.ts、types.ts | 异步有限迭代、取消补齐、reasoning、重入拒绝 |
| Provider | src/provider.ts | HTTP 原始编码/压缩字节、SSE、未知字段、超时/取消 |
| Tools | src/tools/ | 文件、schema、Worker 搜索、shell 进程组 |
| Skills / AGENTS / templates | src/resources.ts | YAML 目录、按需正文、优先级、来源与相对资源边界 |
| Extensions | src/runtime.ts | 显式 ESM 注册工具/观察者 |
| Events / storage | src/events.ts、storage.ts | 必须记录、隔离副本、原子快照、兼容 POSIX flock |
| Runtime / CLI | src/runtime.ts、arguments.ts、cli.ts | 默认组装、resume/inspect、可注入 Model |
| TUI | src/tui/render.ts、cli.ts | 轻量输入与流式展示，保留命令/续行/取消 |
| Web | src/web/、web/static/ | HTTP/SSE、独立 Node 会话子进程、全文检查 |
| 包 | package.json、bin/、scripts/build.ts | Node 24 ESM、JS 与类型声明、静态资源 |

## 验证

22 项离线集成测试通过，覆盖真实文件/shell/HTTP、旧 Python v1 恢复、发布接口和取消。严格类型检查、30 份 Markdown 本地链接检查通过；npm tarball 在仓库外安装后，CLI、Web 页面/资源、工作进程、模型和 trace 验证通过。浏览器完成写文件与 shell 验证的演示；窄屏工具卡片打开检查区时的事件冒泡问题已修复。命令见 [验证](testing.md)。

迁移不消耗真实模型 API；真实模型验收另行使用 e2e 脚本。`.py` 扩展和同步 SDK 调用是明确的 API 迁移点。session/trace 的 schema_version 和 snake_case 字段保持 v1。

## 边界

POSIX shell/锁在 Windows 使用 WSL。完整 shell 输出和单条 trace 记录仍会暂存内存，无硬内存/磁盘配额。恢复使用保存的 system；更新完整项目/资源提示需新会话。没有 MCP、子 agent、自动摘要、长期记忆或公网认证服务。
