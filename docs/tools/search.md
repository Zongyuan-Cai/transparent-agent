# find 与 grep

find 接收 path、glob 和 file/dir 过滤，返回排序后的相对路径，限制条数并注明。跳过常见依赖/版本控制目录，不经 symlink 逃逸。glob 不执行 shell；无匹配是成功。

grep 接收正则、path、可选 glob/ignore_case，返回 `path:line:text`。优先 rg，没有时 Python 回退；无匹配成功，坏正则/执行错误失败，二进制跳过。

rg 的 .gitignore 行为与 Python 回退不同，不承诺一致；两个后端都不读取工作区外 symlink。查询条数与模型字符截断分开。后端和范围记录在结果/metadata；匹配文本可能敏感，trace 不暗中删改。

验收用真实临时目录：空匹配、嵌套、glob、大小写、坏正则、中文、rg/回退、symlink。
