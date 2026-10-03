# Skills

Skill 是专业说明与资源；tool 执行动作；extension 是代码。读取 SKILL.md 不代表执行了全部脚本。

```text
.agents/skills/review-code/
  SKILL.md
  references/checklist.md
  scripts/check.sh
  assets/example.json
```

```yaml
---
name: review-code
description: Review code changes and test behavior. Use for code review tasks.
disable-model-invocation: false
---
```

正文为 Markdown，资源相对 skill 根解析。name 最多 64 字符，用小写字母/数字/单连字符；description 必填，最多 1024。支持 YAML frontmatter；allowed-tools 等元数据不自动变成权限策略。

## 发现

显式 --skill-dir → 项目 .agents/skills → 用户 ~/.config/transparent-agent/skills 和 ~/.agents/skills。目录内排序；同名优先保留先发现者并记录诊断。坏 YAML/名称/缺描述/读取失败跳过并诊断，其他 skill 正常加载。发现不运行脚本。

system 只含 name/description/来源。disable-model-invocation=true 不进自动目录，仅允许用户显式加载。

## 按需加载

模型调用 load_skill({"name":"review-code"})；用户用 `/skill:review-code <任务>`。正文/来源进入工具结果或用户消息，同时产生 skill_loaded。读取只接受 catalog 里已发现的名称。

工作区内资源可用基础工具；工作区外用 load_skill 的可选相对 resource，拒绝绝对路径、`..` 和 symlink 逃逸。执行脚本仍须显式 shell 调用。加载时读实际正文，记录不是启动时的假定副本；重启重新发现目录。

验收：目录不含正文、冲突诊断、坏 YAML、禁自动的显式加载、相对资源、越界、源路径、实际 API 中含加载内容。
