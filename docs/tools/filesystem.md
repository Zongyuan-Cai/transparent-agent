# 文件工具

路径按 workspace resolve，最终必须在其内；拒绝 `..`、绝对路径和 symlink 逃逸。合法的工作区内绝对路径允许。

| Tool | 参数 | 行为 |
| --- | --- | --- |
| read | path、start_line、end_line | 带行号文字，默认前 2000 行 |
| write | path、content | 创建父目录、完整覆盖 |
| edit | path、old_string、new_string、replace_all | 精确替换、小 diff |
| ls | path | 当前层，最多 200 项 |

read 从 1 编号，坏范围报错，二进制/不可解码明确失败；查询范围限制和字符截断分别标注。trace 保存本次读取的完整范围。

write 不自动备份、回滚；向目录写入失败。参数记录保留实际写入文本。edit 的 old_string 不可为空，默认唯一匹配；多匹配要求缩小范围或显式 replace_all，零匹配失败。diff 是摘要，实际写入由替换决定。

ls 顺序确定并说明还有结果；无权限/坏 symlink 不能使进程退出。验收检查真实磁盘：歧义替换、中文、空文件、二进制、越界、symlink、写入结果。
