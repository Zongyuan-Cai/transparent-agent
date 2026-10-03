# 消息与数据模型

归一化类型隔离协议差异；原始 trace 保留未知字段，二者不能替代。

| Message 字段 | 含义 |
| --- | --- |
| role | user / assistant / tool；system 单独保存，provider 编码时加入 |
| content | 文本，assistant 可以只有工具调用 |
| tool_calls | assistant 的有序调用 |
| tool_call_id | tool 结果关联 assistant 中的调用 ID |
| reasoning | API 公开且允许回传的推理文本 |

ToolCall 包含 id、name、原始 JSON 字符串 arguments。先保留字符串，再解析 object；坏参数不得改成 `{}` 执行。StreamItem 的 response 保存正文、reasoning、完整 tool_calls、finish_reason、Usage；text/reasoning 为增量。Usage 为 API 提供的 prompt_tokens/completion_tokens/total_tokens，缺失不估算。

ToolResult 的 output 是模型可见文字，is_error 是失败状态，display 是终端摘要，metadata 是结构化细节，raw_output 保存截断前全文。shell metadata 保存 stdout/stderr/exit_code/elapsed/timeout 和输出原始字节的 base64。trace 同时保存完整和模型结果；metadata 未编码入请求时不算模型输入。

类型由 src/types.ts 的 TypeScript 接口定义。事件与会话使用 UTF-8 JSON，所有恢复数据有版本；未知版本拒绝恢复。可选字段缺失和 null 按协议处理，provider 显式转换 function 消息。验收含 reasoning、多工具和中文的历史往返。
