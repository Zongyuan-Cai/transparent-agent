# 配置

优先级：CLI → 环境 → 启动目录 .env → ~/.config/transparent-agent/.env → 默认；已设置环境不覆盖。

| 配置 | 默认/行为 |
| --- | --- |
| OPENAI_API_KEY | 模型运行必需，help/inspect 不必需 |
| OPENAI_BASE_URL / OPENAI_MODEL | OpenAI 兼容端点 / gpt-4o |
| WORKSPACE_DIR | 当前目录，-C 覆盖 |
| MAX_STEPS | 40 次模型请求/run |
| MODEL_TIMEOUT_S | 300 秒 |
| BASH_TIMEOUT_S / MAX_BASH_TIMEOUT_S | 120 / 600 秒 |
| MAX_TOOL_OUTPUT_CHARS | 30000 字符的模型输出 |
| STREAM_USAGE | true；端点不支持可关闭 |
| TRANSPARENT_AGENT_STATE_DIR | ~/.local/state/transparent-agent |

--state-dir、--trace-dir 覆盖存储根；--resume 指定快照；--skill-dir/--prompt-dir 可重复；--extension 显式 ESM .mjs/.js/.ts 文件。确切参数以 --help 为准。

数字必须有限、正值，整数参数不能接受小数；workspace 存在且是目录，base_url 必须 HTTP(S)。配置错误指出名称，不打印秘钥。资源相对 workspace，存储路径启动 resolve；缺可选目录正常，显式坏目录诊断。新项目安装/编译方式见 [README](../README.md)。
