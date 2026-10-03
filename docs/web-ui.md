# 独立 Web UI

Web 是 Runtime 的另一个客户端，与 TUI 分开启动；后端不导入 tui、Renderer 或终端输入代码。复用相同模型、工具、资源、trace 和会话契约。

## 启动

```bash
transparent-agent-web -C /path/to/project --port 8765
python -m transparent_agent.web -C /path/to/project --port 8765
```

打开 http://127.0.0.1:8765。默认绑定本机，配置和凭据来自服务端原有环境/.env；浏览器不接收 API key。--state-dir、--model、--base-url、--max-steps、--skill-dir、--prompt-dir、--extension 与原 CLI 的含义一致。

## 界面

- 会话列表：新建、选择、恢复本地会话，显示模型/工作区和运行状态。
- 对话：流式回复、可折叠 reasoning、工具活动、继续发送任务、停止当前运行。
- 检查区：按请求/响应/工具筛选轨迹，搜索和分页，点击查看完整事件、请求 body、拼接响应或工具全文；复制 JSON、下载原始 trace。
- 资源：显示可用 skills；用户可用 /skill:name 和 /prompt:name，仍走 runtime 的显式展开流程。

## 后端接口

| 接口 | 含义 |
| --- | --- |
| GET /api/config | 非敏感配置、浏览器写入 token |
| GET /api/sessions | 本地会话元数据 |
| POST /api/sessions | 新建会话（可指定 workspace/model） |
| GET /api/sessions/:id | 快照、运行状态和资源 |
| POST /api/sessions/:id/runs | 提交任务，202 表示已接受 |
| POST /api/sessions/:id/cancel | 请求中断 |
| POST /api/sessions/:id/clear | idle 时清空对话，trace 保留 |
| GET /api/sessions/:id/events | 分页的事件摘要 |
| GET /api/sessions/:id/stream | SSE，按 seq/Last-Event-ID 续传 |
| GET /api/sessions/:id/events/:seq | 全量单条事件 |
| GET /api/sessions/:id/requests/:request_id | 一次请求的原始/归一化输入输出 |
| GET /api/sessions/:id/trace | 下载原始 JSONL |

HTTP 层只负责协议与工作进程管理。每个活动会话由独立进程拥有 Runtime，防止同步模型/工具阻塞 Web 服务；同会话不允许两个 run。停止使用 POSIX SIGINT，让原 loop 清理模型流和 shell 进程组。默认最多保留 8 个 worker，创建/恢复更多会话时释放最久未用的空闲 worker；全忙时返回 503。会话文件和 trace 保留，恢复不重放工具。

## 记录与断线

浏览器断线不停止任务。SSE 从已落盘 trace 读取摘要，原始大字段按需读取；完整数据始终在 trace。重连用 seq 去重。工作进程异常退出显示失败，继续前从完整快照恢复并修补悬空调用。UI 状态通知与 agent trace 事件分别传输。

events 的 after 是已读 seq，limit 为 1–500，默认 200；stream 同时接受 after 和 Last-Event-ID，取较大的游标。摘要文本最多 8192 字符，详情和下载不按摘要截断。requests 接口先按字节拼接所有响应 chunks，再解码 UTF-8/gzip/deflate，始终返回原始 base64；不支持或不完整的压缩内容显示 decode_error，事件全记录仍可检查。选中的请求详情随新增响应刷新。

写入正文上限 1 MiB。HTTP 创建接受 workspace/model；运行接受 text。异步任务返回 202，实际错误/取消通过 status 和 trace 反馈；忙会话或锁冲突返回 409。空闲的跨服务会话可直接读取快照与资源目录，首次运行才启动 worker。每次运行的执行权限与本地 agent 一致。

trace 索引和浏览器保留当前已读的事件摘要，完整工具输出与请求聚合暂存内存；没有增加硬内存/磁盘配额。非常大的 trace 会增加初次载入时间与内存占用。

## 本地边界

服务只绑定 loopback；校验 Host/Origin，写接口要求同源 token 和 JSON，禁止任意路径静态文件访问。任务正文按纯文本/安全 Markdown DOM 展示，不执行模型返回的 HTML。不要把这个无账号的本地服务当成公网服务。POSIX shell/取消能力在 Windows 使用 WSL。

验收包括真实本地 HTTP 模型服务、Web 提交与停止、跨服务恢复、SSE 续传、trace 完整性、同源边界、安装包静态资源，以及浏览器实际交互和窄屏布局。
