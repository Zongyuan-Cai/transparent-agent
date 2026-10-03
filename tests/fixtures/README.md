`python-v1` 由 Python 版本 `378925b8f6633fe08272ed0567c38b28cee0a086` 的
`SessionStore.save` 和 `TraceRecorder.emit` 生成；仅工作区和 trace 路径使用占位符。
它包含未完成的工具调用，用于确认迁移只修复会话，不重放副作用。
测试替换路径后通过 TypeScript Runtime 恢复，保留旧 system、追加连续事件。
