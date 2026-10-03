# 基础提示与模板

基础提示定义编码角色、迭代、工具限制和最终汇报，不硬编码用户任务或 skill 正文。

模板是具名 Markdown 文件，如 `.agents/prompts/review.md`：

```markdown
检查改动中的可复现行为问题，给出文件位置：
$ARGUMENTS
```

`/prompt:review src/parser.ts` 显式展开。$ARGUMENTS 仅文本替换，不执行 shell；没有占位符时参数追加。发现优先级为显式目录、项目、用户配置 prompts；同名先者优先并诊断。prompt_expanded 保存名称/来源/参数/最终文字，实际请求携带展开内容。

模板复用问题表达，skill 复用流程和资源；模板建议 skill 不意味着自动执行。验收有/无参数、有/无占位符、中文、冲突、未知名和展开一致。
