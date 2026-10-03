# 错误与取消

| 类别 | 处理 |
| --- | --- |
| 工具参数/文件/命令失败 | 回填 tool error，模型可纠正 |
| provider 认证/网络/协议失败 | 停 run，session 可再提交 |
| 用户取消 | 清理流/进程组，补齐调用后回输入 |
| max_steps | 不再请求，保留完成历史 |
| 观察者失败 | 容错通道隔离 |
| trace/session 写失败 | 停 run，CLI 报错 |
| 坏 skill / 坏显式扩展 | skill 诊断跳过；扩展停止启动 |

不自动重试 API、工具、文件写入；用户重试为新 run。请求期间取消不执行不完整参数；工具期间取消清理进程组并补结果。KeyboardInterrupt 不可被一般 Exception 吞掉。

finally 清理/保存；trace 已坏时不保证 finish 落盘。错误信息不打印配置秘钥。取消不自动回滚磁盘改动。
