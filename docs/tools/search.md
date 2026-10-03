# 搜索工具

find 接收 glob pattern、path 和可选 type（file/dir/any），递归搜索名称；包含 `/` 时按搜索根相对路径匹配，否则按 basename。结果为 workspace 相对路径，目录带 `/`。

grep 接收 JavaScript 正则 pattern、path、可选 glob/ignore_case，返回 `path:line:text`。在可终止的 Node Worker 中扫描，复杂正则也能响应 AbortSignal。语法不承诺与旧 rg/Python 正则兼容；不按 .gitignore 过滤。

两者跳过 .git、node_modules、虚拟环境、dist/build 等生成目录，不递归 symlink 目录；grep 不跟随文件 symlink。初始 path 仍核验 workspace 边界。find/grep 最多 200 个结果；grep 每行最多 300 字符、每文件最多 2 MB，二进制/坏 UTF-8/不可读文件跳过。扫描有 30 秒期限，查询范围与模型字符截断分别标注。

grep metadata.backend 为 node-worker。无匹配是成功，坏正则失败；超时/取消终止 worker。find 的扫描在异步文件操作之间检查取消。匹配文本可能敏感，trace 保留实际返回结果。

验收嵌套、glob、大小写、中文、二进制、坏正则、symlink，以及会卡住主线程的正则在 worker 中可取消。
