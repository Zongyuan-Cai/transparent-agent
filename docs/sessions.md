# 会话与恢复

session.json 保存可继续对话的历史；trace 保存执行细节。禁止把 trace 自动重放为工具操作。

快照包含 schema_version、session_id、workspace、model、system、完整 messages（含 tool_calls/reasoning/results）。新 CLI 默认新会话；--resume 指定 session.json。核验版本和 workspace，显式模型切换可更新快照并记录。

user 入历史、完整 assistant/工具组和 run 退出时保存；同目录临时文件、flush/fsync、原子 replace，写失败停止。一个 session 只允许一个活动进程持锁，防止竞争。

崩溃可能已经产生工具副作用但未保存结果。恢复只用完整快照；悬空 tool_call 补「执行状态未知，未重放」错误并诊断，让模型重新检查。trace 追加继续 seq，不覆盖；尾部不完整 JSON 报损坏，不自动掩盖。

/clear 保存空历史并记录 session_cleared，旧 trace 不删除。usage 是每个 run 的统计，恢复不计为本轮消耗。

验收进程间恢复、roundtrip、坏版本/JSON、workspace 不符、悬空调用、原子写、锁和 clear。
