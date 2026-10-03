# 透明记录

默认每个 CLI session 产生 trace.jsonl，启动和单次 stderr 显示路径。编辑器、JSONL 脚本或 `--inspect <trace>` 可读，无需重写 TUI。

| 视角 | 保存内容 |
| --- | --- |
| Agent | system/历史/schema、归一化响应 |
| Transport | 实际请求 body、响应 headers/body chunks |
| Execution | 原参数、完整/回填结果、metadata |

未知响应字段、HTTP 错误正文和截断前工具结果必须保留。body_base64/chunk base64 拼接后重建完整 body，解析后的便利字段不能替代字节。

Envelope 关联 session/run/request/call，seq 跨恢复继续。每行追加后 flush。默认 `~/.local/state/transparent-agent/sessions/<id>/`，state-dir/trace-dir 覆盖；本地文件用户私有权限，Git 忽略。

api_response_end 的 complete 表示读到 HTTP body 末尾，closed 表示客户端关闭流（包括 SDK 遇到 [DONE]），error/cancelled 表示读取失败/取消。原始记录包含客户端已经收到的全部 body chunks；取消后尚未返回的字节无法记录。Content-Encoding 为压缩格式时需按响应 header 解压字节再读 SSE。

## 凭据例外

Authorization、Proxy-Authorization、API key、cookie/token/secret 类 header 及 URL 认证参数显示 [REDACTED]。请求和返回正文原样记录；用户主动把秘钥写进输入/工具输出时也会记录。trace 不自动上传，分享需另制脱敏副本。

磁盘满、权限/序列化失败停止 run，不能悄悄继续；已有副作用无法撤销，最后失败结果可能未落盘。inspect 离线显示记录，不调用模型、不执行工具。恢复也不重放。审计不等于确定性重新运行。

验收实际 body 与 mock transport 接收一致、未知字段、长结果可找回、错误/取消原始流、认证头/URL 不含 key。
