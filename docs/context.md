# 上下文

模型只知道实际请求内容，文件在磁盘或事件在 trace 中不等于进入了上下文。

System 顺序：基础角色/工具规则 → workspace/环境 → 项目根到 workspace 的祖先 AGENTS.md → skill 元数据目录 → template 命令目录。项目指令来源记录实际路径和正文；不扫描兄弟目录。最近的规则更具体，但不能升级为用户授权。

历史保留全部完成消息；只有工具输出有模型字符上限，system/用户内容不静默删除。暂不自动摘要；上下文超限明确报 provider 错误，用户可新会话。未来压缩必须记录前后、原文范围和摘要请求。

启动 context_loaded 保存来源/正文，每次 request_start 保存最终 system/messages/tools。恢复用保存的 system，避免新 AGENTS.md 悄悄改变历史语义；资源目录重新发现，完整 system 更新需新会话。

验收嵌套规则顺序、空项目、中文路径、元数据、恢复 system 稳定、API body 与快照相符。
