# 实现状态

文档库先完成并独立提交（16fb107），然后才实施重构。接口保持原有同步 stream/send/run 方式；默认运行时增加持久透明记录与资源能力。

| 组件 | 实现 | 结果 |
| --- | --- | --- |
| Loop / 数据模型 | agent.py、models.py | 有限迭代、取消补齐、reasoning、重入拒绝 |
| Provider / HTTP | providers/openai_chat.py、trace.py | 实际 body/原始 chunks、未知字段、错误/断流、关闭客户端 |
| 基础工具 | tools/ | schema 校验、全文/回填区分、双流原始字节、进程组清理 |
| Skills | resources.py | YAML 发现、优先级、lazy load、显式调用、相对资源边界 |
| AGENTS / templates | resources.py、prompt.py | 祖先指令、来源记录、显式模板替换 |
| Extensions | extensions.py | 显式 Python 模块，注册工具/只读事件观察者 |
| Events / trace | events.py、trace.py | 全局序号/关联、观察者隔离、必需记录失败停止 |
| Sessions | sessions.py、storage.py | 原子快照、跨进程恢复、锁、悬空调用不重放 |
| Runtime / CLI | runtime.py、cli.py | 默认组装、路径提示、inspect/resume、可注入 Model |
| TUI | tui/app.py | 保留布局/输入/渲染，仅添加资源与 trace 命令 |

相对模块路径以仓库 transparent_agent/ 为根。完整接口契约在组件文档中；示例在 tutorial.md 和 examples/skills/test-fix/。

## 验证

- 重构前：143 个原有测试通过。
- 重构后：184 个测试通过，覆盖真实工具、HTTP 字节、长输出、取消、资源、存储失败和两个独立 CLI 进程恢复。非零 shell 退出现在按设计标记为错误观测，相应原测试同步更新；模型仍可根据结果继续修正。
- scripts/check_docs.py 检查文档本地链接，git diff --check 检查补丁格式。
- 独立 Python 虚拟环境成功构建并安装 transparent_agent-0.1.0 wheel，仓库外运行安装后的 CLI --help 成功，pip check 通过。旧系统 setuptools 59.6 无法正确读取项目元数据，应使用 README 的虚拟环境安装步骤。
- API 验收使用本地 HTTP endpoint/fake Model，没有调用真实外部模型，未执行付费真实模型 e2e。

## 明确边界

文件工具查询有 5 MB/行数等范围限制，grep 有结果/行预览/扫描限制；trace 保存实际返回的完整查询结果，不能声称已读取磁盘全文。原始响应记录到客户端关闭为止，未收到的取消后字节不可记录。认证 header/URL 参数脱敏，正文不自动脱敏。

同步 shell 进程组实现适用 POSIX，Windows 使用 WSL。完整 shell 输出暂存内存并写 JSONL，未增加 artifact streaming 或硬内存限制。恢复使用保存的 system，更新完整项目/资源目录提示需新会话。

MCP、子 agent、长期记忆、自动压缩、树形 session、Web/RPC、OS 沙箱属于可选演进，不宣称已内置。
