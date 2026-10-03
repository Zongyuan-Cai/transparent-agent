# 模型与 Provider

`Model.stream({ system, messages, tools, signal })` 返回 `AsyncGenerator<StreamItem>`：可有多段 text/reasoning，最后必须恰好一份 response。core 依赖协议，不依赖 HTTP 实现。取消通过 AbortSignal；可选 close 释放自定义模型资源。

```typescript
interface Model {
  name: string;
  stream(input: {
    system: string; messages: Message[]; tools: ToolSpec[]; signal: AbortSignal;
  }): AsyncGenerator<StreamItem>;
  close?(): void | Promise<void>;
}
```

内置 `ChatModel` 使用 Node HTTP/HTTPS 调用 OpenAI 兼容 `/chat/completions`。system 作为首条消息，tool_calls 编码为 function，tool 结果保留关联 ID，公开 reasoning 以 reasoning_content 回传。默认请求 usage；端点不支持可关闭 STREAM_USAGE。

流解析跨网络片段增量解码 UTF-8，识别 SSE data 帧和 [DONE]。工具片段按 index 汇集 id/name/arguments，完整响应后才允许执行。未知 chunk 字段仍原样写入 api_response_parsed。缺 [DONE]/finish_reason、无效 JSON、无效调用 ID、网络断流与非成功 HTTP 都报 ModelError。

传输前记录实际 method/URL/headers/body；接收时先记录压缩原始字节，再解压 gzip/deflate/br 并解析。原始 body 是 HTTP 客户端暴露的应用字节，不包含 TLS 包或 HTTP chunk framing。默认没有自动重试；每次请求带独立 request_id。

总请求时间受 request_timeout 限制；AbortSignal 销毁请求、响应和流。trace 写失败传播 PersistenceError，不能包装为普通网络错误。Agent 保存完成响应后才执行工具，部分流仅留在 trace/UI。

验收用真实本地 HTTP 服务比对服务器接收 body 和 trace body，检查跨字节中文、碎片化调用、reasoning/usage/未知字段、压缩、错误、断流、超时和取消。
