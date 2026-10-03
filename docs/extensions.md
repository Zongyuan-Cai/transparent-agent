# 扩展

默认不导入项目代码；`--extension file.mjs`、`.js` 或 `.ts` 显式加载 ESM。TypeScript 扩展须使用 Node 原生类型擦除支持的语法；普通 JavaScript 文件须处于 ESM 包作用域，或使用 `.mjs`。

```typescript
import type { Tool, EventSink } from 'transparent-agent';

export function register(api: {
  registerTool(tool: Tool): void;
  subscribe(observer: EventSink): void;
}) {
  api.registerTool({
    name: 'hello', description: 'Return a greeting',
    parameters: { type: 'object', additionalProperties: false },
    async run(_args, context) {
      context.signal.throwIfAborted();
      return { output: 'hello' };
    },
  });
  api.subscribe({ emit(event) { /* 同步读取独立事件副本。 */ } });
}
```

实际示例见 [event_logger.mjs](../examples/event_logger.mjs)。register 可返回 Promise，启动会等待。重名、坏 schema、导入/register 失败停止启动。工具走统一校验、截断与 trace；观察者异常隔离，PersistenceError 始终传播。extension_loaded 记录路径和新增工具。

扩展有当前进程权限，API 不是沙箱。旧 Python `.py` 扩展不自动转换，需要将注册方法和工具实现迁移到 ESM；register_tool 改为 registerTool，send/tool 执行可异步，取消改为 AbortSignal。

MCP、子 agent、RAG 可通过 tool 或嵌入 Runtime 扩展，当前不内置。最小注册接口不提供 provider 注册或静默改写请求 hook。验收显式加载、异步注册、冲突、schema/trace 和观察者隔离。
