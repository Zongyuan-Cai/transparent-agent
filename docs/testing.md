# 验证

主要验收离线，不消耗 API；测试行为与边界。

| 层 | 证据 |
| --- | --- |
| Loop / Runtime | 工具反馈、上限、取消补齐、lazy skill、保存上下文 |
| Provider | 实际 body 比对、UTF-8 碎片、工具片段、reasoning/usage/未知字段、压缩、错误/取消 |
| Tools | schema、磁盘结果、CRLF、UTF-8、symlink、可取消正则、双流/超时/后台进程 |
| Session / Events | Python v1 样本、恢复不重放、flock、权限、连续序号、修改隔离、trace 失败停副作用 |
| CLI | 单次 stdout/stderr、排队输入、续行、resume/inspect、SIGINT |
| Web | 真实 HTTP、独立会话进程、忙拒绝、取消、SSE 重连、全文、下载、边界和空闲回收 |
| 发布 | npm tarball 仓库外安装，命令与静态页面运行 |

```bash
npm run build
npm run typecheck
npm test
npm run check:docs
npm pack
node scripts/check-install.ts ./transparent-agent-0.2.0.tgz
git diff --check
```

测试使用 Node 内置 test runner。`tests/fixtures/python-v1` 由旧版序列化器生成，测试仅替换路径；当前测试运行无需 Python。锁绑定安装时仍需要 node-gyp 构建环境。

`npm run demo -- --port 8766` 启动模拟 HTTP 模型和 Web。真实 API 验收为 `node scripts/e2e.ts`，需要有效配置；必须区别模拟验证与真实模型验证。发布前确保 diff 不含 .env、真实会话/trace 和构建产物。
