# 扩展

默认不自动导入项目代码；--extension file.py 显式加载。最小接口：

```python
def register(api):
    api.register_tool(MyTool())
    api.subscribe(my_sink)
```

工具名称冲突/导入/register 失败停止启动并指出来源。工具走统一校验/executor/trace，观察者容错。extension_loaded 记录来源、注册结果。Python 扩展有当前进程权限，这个 API 不是沙箱。

MCP 可适配成 tool；子 agent 可以启动子 runtime 并关联 parent_call_id；planner/TODO 用文件/工具；RAG/记忆通过显式检索把来源回填。上述为设计位置，当前不内置。provider 扩展另需注册接口，最小 API 不自动支持。

未来上下文/结果变换 hook 必须记录前后，并与通知区分；当前不提供静默改请求 hook。验收显式注册、坏扩展、冲突、schema/trace、观察者修改隔离。
