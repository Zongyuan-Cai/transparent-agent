# 验证

主要验收离线，不花 API 配额；测试行为，不镜像实现。

| 层 | 必需证据 |
| --- | --- |
| Loop | 工具迭代、失败纠正、上限、取消补齐、重入 |
| Provider | 接收 body=trace body，SSE index/reasoning/usage/未知字段/错误 |
| Executor | schema 拒绝且无副作用，完整/截断结果 |
| Workspace/Shell | symlink、真实进程超时/取消、后台 pipe |
| Resources | 按需 skill、冲突、AGENTS 顺序、模板 |
| Session | roundtrip、恢复不重放、版本/锁/原子写 |
| Events | 普通 sink 容错、trace 失败停止、修改隔离 |
| CLI/TUI | 旧交互、stdout/stderr、inspect |

```bash
python3 -m pytest -q
python3 -m transparent_agent --help
```

离线端到端使用 fake Model，但真实文件/shell/skill、trace/session，恢复后再继续。provider 另用 mock HTTP 验证真实编码。真实 API 验收用 scripts/e2e.py，需要有效 key；未运行必须明确说明。

Markdown 链接要存在，示例参数在 --help；发布前 diff 不含 .env/trace/session。设计单独提交，实现后填写状态与验证结果。
