# 事件协议

事件通知不能改变核心控制流。trace envelope 包含 schema_version=1、session_id、run_id（会话级可为空）、递增 seq、type、UTC ISO timestamp、data。request_id/call_id 位于关联事件 data。

| 类型 | 内容 |
| --- | --- |
| session_start / session_resumed / session_cleared | workspace、模型、状态变化 |
| context_loaded / resource_diagnostic | 来源正文/加载诊断 |
| skill_loaded / prompt_expanded / extension_loaded | 资源来源/结果 |
| agent_start / user_message | run、本轮输入 |
| model_request_start | 完整 system/messages/tools、step/request_id |
| api_request | 实际 method/url/headers/body_base64 |
| api_response_start / chunk / end | 状态、headers、字节、终止 |
| api_response_parsed | Provider 解析的完整 chunk，保留未知 JSON 字段 |
| model_text_delta / model_reasoning_delta | 展示增量 |
| model_response | 归一化完整响应/usage |
| tool_call_start / end | 原参数、全量/回填结果、metadata、失败/取消、耗时 |
| agent_error / finish | 类别、停止原因、统计 |

EventSink 为同步 emit(event)；内存 sink 可用于 SDK 测试。EventBus 先调用必须成功的 recorder，再向每个 observer 投递独立副本。普通观察者异常记录诊断；PersistenceError 始终传播。默认 Runtime 使用 TraceRecorder，TUI/扩展观察者放容错通道。

投递深拷贝，观察者不能修改请求或污染另一个观察者。慢同步观察者会增加耗时。验收连续 seq、多 run、Unicode、恶意修改、坏观察者运行、坏 trace 停止。
