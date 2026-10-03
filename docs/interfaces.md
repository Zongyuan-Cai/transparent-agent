# CLI、SDK、TUI 与 Web

```bash
transparent-agent -C ./project
transparent-agent -C ./project -p '修复测试'
transparent-agent --resume /path/to/session.json
transparent-agent --inspect /path/to/trace.jsonl
```

print 助手文字到 stdout，工具与记录路径到 stderr。inspect 不需要 key。退出码区分成功/失败/取消，步数上限可发现。

SDK Agent 接收 Model、ToolRegistry、context、system、EventBus、可选 resources/save。无落盘 recorder 的内存 EventBus 不能宣称有持久透明记录。Runtime.create(config, options, observer?, model?) 负责默认存储与组装，支持注入 Model 做离线测试。

await send(user_text, signal?)、clear()、setModel(name)、history 和统计为公开接口。send 重入拒绝，取消用 AbortSignal；clear/模型切换只能在 idle。finally 中 await Runtime.close()，关闭模型、trace 和会话锁。

保留 TUI 的 Enter、反斜杠续行、Ctrl+C、Ctrl+D、/help、/clear、/status、/model、/exit、流式 renderer。只加 /skills、/skill:name、/prompt:name、/trace 的轻入口；不改布局、主题、编辑器。旧 renderer 忽略新事件，持久化由 runtime 负责。

验收原有 UI 测试、print 分流、新命令、离线 inspect。

独立 Web 入口为 transparent-agent-web / node bin/web.mjs。Web 后端直接使用 Runtime，不导入终端 CLI/TUI；共享 arguments.ts 的配置解析。按会话隔离进程，以 HTTP 提交和 SSE 展示持久事件。服务使用相同状态目录，TUI 持锁的会话不能同时在 Web 运行。见 [Web UI 契约](web-ui.md)。
