# TypeScript 迁移

分支：codex/typescript-rewrite。Python 版本保留在 main 的 378925b 提交；这个分支用 Node.js / TypeScript 完整替换运行时，不通过子进程调用 Python。

## 组件与约束

- 严格 TypeScript、ESM、Node.js 24，构建后发布 JavaScript 与 SDK 类型声明。
- Agent.send、Model.stream 与工具异步化，Model 使用 AsyncGenerator，取消统一使用 AbortSignal。一次会话仍只运行一个任务，工具仍串行。
- API provider 使用 Node HTTP/HTTPS，记录实际发出的 body 和收到的响应字节，再做 SSE 解析；未知字段、reasoning、usage 和 tool-call fragments 保留。
- 文件、搜索、shell、skills、AGENTS、模板、扩展、事件、trace、会话、CLI/TUI、Web 后端全部迁移。已有 Web 静态界面保留。
- session.json 与 trace.jsonl 保持 schema_version=1 和字段名称，已有 Python 会话可恢复，历史工具不重放。POSIX flock 与 Python 会话锁互斥。
- Python 扩展改为显式 ESM JavaScript/TypeScript 扩展，register(api) 注册工具与事件观察者；不自动执行旧 .py 模块。
- TUI 保留 Enter、反斜杠续行、Ctrl+C、Ctrl+D、原命令与流式输出。Web 保留 URL、HTTP/SSE 接口、界面及按会话隔离的工作进程。

## 验收

严格类型检查；真实文件与 shell 测试；本地 HTTP SSE/压缩/未知字段/字节追踪；取消和超时的进程组清理；资源优先级和越界拒绝；快照、锁和旧版本文件恢复；CLI/TUI 分流与恢复；Web 创建/发送/取消/SSE/完整记录；npm tarball 在仓库外安装、运行和加载静态资源。使用本地模拟模型，不调用付费外部 API。

运行依赖为 Node、JSON Schema/YAML/glob 解析和 POSIX flock 绑定；Linux/macOS 的 shell 语义保留，Windows 使用 WSL。Python 不再是运行依赖。构建原生 flock 绑定时需要平台的 C++ 构建工具与 node-gyp 的构建环境。
