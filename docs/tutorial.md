# 上手示例：从工具到 Skill

命令在仓库根运行。模型调用需要 key、会消耗 API；离线测试不需要。

```bash
python -m transparent_agent -C /path/to/project \
  --skill-dir /absolute/path/transparent-agent/examples/skills \
  -p '/skill:test-fix 复现并修复失败测试'
```

agent_start 后 skill_loaded 记录正文与来源；user_message 携带完整说明。第一份 model_request_start/api_request 含这些内容，随后 read/bash/edit 等参数和结果全部记录。

自动选择时只提交任务，模型先看到描述，再调用 load_skill。AGENTS.md 和 skill 来源分别在 context_loaded/skill_loaded。

```bash
python -m transparent_agent --resume /path/to/session.json -p '检查边界情况'
python -m transparent_agent --inspect /path/to/trace.jsonl
```

model_request_start 是可读快照，api_request.body_base64 是真实编码。解码后 tools 为 OpenAI function 格式，形状与归一化快照不同。tool_call_end.full_output 是全文，output/model_content 是回填文字；metadata 有 shell 双流/退出状态。

## SDK

```python
from pathlib import Path
from transparent_agent.config import Config, load_dotenv
from transparent_agent.runtime import Runtime, RuntimeOptions

load_dotenv()
config = Config.from_env(workspace='/path/to/project')
with Runtime(config, options=RuntimeOptions(state_dir=Path('/path/to/local-state'))) as runtime:
    result = runtime.agent.send('检查测试并汇报')
    print(result.stop_reason, result.text)
    print(runtime.trace_path, runtime.session_path)
```

测试可传 model=fake_model，实际工具/存储仍工作；Agent+NullSink 则只有内存对话，没有默认落盘。
