# bash

bash 在 workspace 或合法子目录启动独立 shell，参数 command、可选 cwd/timeout。一次 cd/export 不影响下次调用。

结果包含 stdout/stderr/exit_code/elapsed/timeout；非零退出和超时为工具错误。trace 保留完整双流输出及截断回填文本。

进程生命周期：新进程组 → 并行 drain 双流 → 等待退出/超时/取消 → 必要时终止整个组 → 等待清理 → 收集结果。shell 正常退出但后台子进程继承 pipe 时也要清理，避免读取无限等待。Ctrl+C 不留下继续工作的子进程。

timeout 必须有限、正值、不超过配置上限。取消不撤销已有文件操作。cwd 检查不是沙箱：shell 可以访问网络和其他目录、继承环境；需要隔离时运行整个 agent 于 OS 沙箱/容器。

完整输出占内存/磁盘，当前适合本地有限任务；未来可流入 artifact，但不能静默丢记录。验收真实进程：双流、非零、超时、取消、子进程、后台 pipe、长输出、cwd。
