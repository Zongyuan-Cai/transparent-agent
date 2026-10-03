# Model 与 Provider

Model 协议的 stream(system, messages, tools) 同步迭代 TextDelta/ReasoningDelta，并以唯一 ModelResponse 结束。core 不依赖 SDK。Provider 接收 model、base_url、认证、超时、usage 开关与 sink；负责编码、流聚合、错误映射、取消和幂等 close。保留同步接口使当前 TUI 和 SDK 调用方式稳定。

## OpenAI 兼容 Chat Completions

POST `<base_url>/chat/completions`，body 包含 model/messages/tools/stream，按配置加入 stream_options.include_usage。实际 body 由 transport 记录，不能从 core 快照猜测。

- tool delta 按 index 聚合 ID、名称、参数，最终顺序按 index。
- reasoning 和正文分开；兼容协议要求时回传 reasoning_content。
- 最后 choices 为空的 usage 帧必须处理。
- 缺失 ID/名称、异常结束、length/content_filter 或断流不能执行截断工具参数。

## 原始传输

SDK 解析前保存 method、URL、认证脱敏 headers、请求 body；响应保存 status/headers/每个 body chunk。字节用 base64 保留，UTF-8 跨 chunk 也可重建。成功、非 2xx、解析错误、断流、取消都记录。

这里的原始字节是 HTTP 客户端暴露的 body，不是 TLS 包或 HTTP/2 帧。关闭 SDK 隐式重试；未来重试要单独 request_id 和 retry 事件。

新增适配器：实现协议、说明归一化损失、接入同等 trace、运行 fake transport 验收、显式选择。Anthropic/Responses 不能仅改 URL 假装 Chat Completions 兼容。
