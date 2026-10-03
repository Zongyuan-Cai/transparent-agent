# 上手示例：从工具到 Skill

先 `npm ci && npm run build`。模型调用需要有效 key；离线测试和演示不需要。

```bash
node bin/agent.mjs -C /path/to/project \
  --skill-dir /absolute/path/transparent-agent/examples/skills \
  -p '/skill:test-fix 复现并修复失败测试'
node bin/agent.mjs --resume /path/to/session.json -p '检查边界情况'
node bin/agent.mjs --inspect /path/to/trace.jsonl
```

显式调用产生 skill_loaded，user_message 和实际模型请求携带正文。自动选择只展示描述，模型再调用 load_skill。来源分别记录在 context_loaded/skill_loaded。

model_request_start 是归一化快照，api_request.body_base64 是实际编码。tool_call_end.full_output 为全文，output/model_content 为回填文字；shell metadata 保存双流、退出状态和原始字节。

## SDK

在源码根的 ESM 模块中：

```typescript
import { Runtime, configFromEnv, loadDotenv } from './dist/index.js';

loadDotenv();
const config = configFromEnv({ workspace: '/path/to/project' });
const runtime = await Runtime.create(config, { state_dir: '/path/to/local-state' });
try {
  const controller = new AbortController();
  const result = await runtime.agent.send('检查测试并汇报', controller.signal);
  console.log(result.stop_reason, result.text);
  console.log(runtime.trace_path, runtime.session_path);
} finally {
  await runtime.close();
}
```

安装为依赖后从 `transparent-agent` 导入同样的公共 API。第三个参数可传事件观察者，第四个参数可注入 fake Model；实际工具/存储仍工作。每次都用 finally 关闭 runtime，释放 trace 和会话锁。

自定义工具遵循 [工具协议](tools/README.md)，通过 [ESM 扩展](extensions.md) 注册。Web 使用同样的 Runtime，入口为 `npm run web -- -C /path/to/project`。
