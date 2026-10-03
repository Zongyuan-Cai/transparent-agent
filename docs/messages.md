# 消息与数据模型

归一化类型隔离协议差异；原始 trace 保留未知字段，二者不能替代。

| Message 字段 | 含义 |
| --- | --- |
| role | system / user / assistant / tool |
| content | 文本，assistant 可以只有工具调用 |
| tool_calls | assistant 的有序调用 |
| tool_call_id / name | tool 结果的关联 |
| reasoning | API 公开且允许回传的推理文本 |

ToolCall 包含 id、name、原始 JSON 字符串 arguments。先保留字符串，再解析 object；坏参数不得改成 `{}` 执行。ModelResponse 保存正文、reasoning、完整 tool_calls、finish_reason、Usage。Usage 为 API 提供的 input/output/total tokens，缺失不冒充估算值。

ToolResult 的 output 是模型可见文字，is_error 是失败状态，display 是终端摘要，metadata 是结构化细节，raw_output 保存截断前全文。shell metadata 保存 stdout/stderr/exit_code/elapsed/timeout 和输出原始字节的 base64。trace 同时保存完整和模型结果；metadata 未编码入请求时不算模型输入。

事件与会话用 UTF-8 JSON，显式转换 dataclass，所有恢复数据有版本。未知版本拒绝恢复。验收：含 reasoning、多工具和中文的历史往返后能编码同样的请求。
