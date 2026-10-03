# 工具协议与 Executor

Tool 包含 name、description、parameters（JSON Schema）、同步 run(arguments, context)。Context 提供 workspace、输出上限、超时和受控资源，不暴露 agent。

名称唯一，重复注册启动失败。Registry 同时管理 schema 和实现；只能执行本次已公布工具。内置 read/write/edit/ls/find/grep/bash，存在 skill 时加入 load_skill。

执行顺序：记录原始 call → 查 registry → 解析 JSON object → 校验 required/type/enum/范围/额外字段 → execute → 标准化失败 → 保留全文 → 截断模型结果 → end 事件 → 历史。

未知工具、坏 JSON、缺参数、类型错误、文件不存在和命令失败均产生 is_error=true 的观测，允许模型纠正。bool 不能通过 integer/number，NaN/Infinity 非法。预期失败不给模型 Python traceback。

字符上限只约束模型输入，不丢 trace 全文。工具的分页/匹配条数是查询范围，另行说明。取消传播 KeyboardInterrupt，由 loop 补齐调用；shell 用 ToolInterrupted 携带已收集输出。新增工具必须验证正常、参数错误、失败、取消、越界和长输出。
