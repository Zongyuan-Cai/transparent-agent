# CLI、SDK 与 TUI

```bash
transparent-agent -C ./project
transparent-agent -C ./project -p '修复测试'
transparent-agent --resume /path/to/session.json
transparent-agent --inspect /path/to/trace.jsonl
```

print 助手文字到 stdout，工具与记录路径到 stderr。inspect 不需要 key。退出码区分成功/失败/取消，步数上限可发现。

SDK Agent 接收 Model、Registry、ToolContext、system、sink、可选 session store。可用 fake Model/NullSink，无 CLI 默认 trace 时不能宣称有持久透明记录。runtime 支持注入 Model 做离线端到端测试。

send(user_text)、clear、set_model、history 和统计通过公开接口。send 重入拒绝，取消用 KeyboardInterrupt；clear/模型切换只能在 idle。Runtime 支持上下文管理器，退出关闭客户端、trace 和会话锁。

保留 TUI 的 Enter、反斜杠续行、Ctrl+C、Ctrl+D、/help、/clear、/status、/model、/exit、流式 renderer。只加 /skills、/skill:name、/prompt:name、/trace 的轻入口；不改布局、主题、编辑器。旧 renderer 忽略新事件，持久化由 runtime 负责。

验收原有 UI 测试、print 分流、新命令、离线 inspect。
