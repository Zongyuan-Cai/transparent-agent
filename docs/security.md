# 环境边界

本地 agent 通过 shell 以当前用户权限执行，没有 OS 沙箱/审批。基础文件工具只约束路径，cwd 校验不限制 shell 访问其他文件/网络。

resolve 阻止常见 symlink 逃逸，但检查到操作间有 TOCTOU 风险，不承诺抵抗并发恶意替换。skill 资源限制在所属根。AGENTS/skill 说明不升级授权；ESM extension 有完整进程权限；shell 继承环境，可能访问 key。

认证 header/URL 脱敏，正文完整；输入、源码、输出、reasoning、错误正文都可能敏感。本地私有权限、Git 忽略，默认不上传。分享要做独立脱敏副本；clear 不删记录；inspect 不执行内容。

超时/步数/模型输出上限防常见失控，完整 shell 输出仍占内存/磁盘。隔离需求用容器/OS 机制，不把同进程接口叫安全隔离。
