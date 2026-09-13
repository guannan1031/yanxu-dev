# v0.8：一键研发交付工作流

`workflow` 把仓库体检、需求任务合同、PR/CI 事实采集和证据新鲜度核验串成一次带状态的执行。它面向已经使用 GitHub、CI 和测试的小型研发团队，减少开发者在多个命令和页面之间整理交付证据的人工操作。

## 开发前运行

```bash
python -m yanxu workflow "Add a safe pagination endpoint" \
  --repo /path/to/repo --output runs/workflow
```

项目体检通过后，输出 `task.json`、`task.md`、执行事件和统一的 HTML/JSON/Markdown 报告，状态为 `READY_FOR_IMPLEMENTATION`。项目规则、README、测试、CI 或密钥边界缺失时，状态为 `BLOCKED`，且不会继续生成任务合同。

## PR 创建后运行

```bash
python -m yanxu workflow "Add a safe pagination endpoint" \
  --repo /path/to/repo --github-repo owner/repo --pr 12 \
  --output runs/workflow
```

该模式会采集 PR diff、checks、status 和 review，生成 `evidence.json`，随后再次采集并生成 `verify.json`。PR 或 CI 有阻断、证据在两次采集间变化时返回 `BLOCKED` 和退出码 2；事实完整且未变化时返回 `READY_FOR_MANUAL_REVIEW`。

## Agent 工作流与验收

输入是需求文本、本地 demo 仓库，以及可选的 GitHub repo/PR。确定性工作流调用 Repository Doctor、Task Contract、GitHub 只读采集和 Evidence Verify；输出包含每个阶段的时间、当前状态、下一步和原始证据。验收要求是阻断即停止、同一次执行内完成版本复核、所有产物可复查，并始终保持 `remote_modified=false` 与 `auto_merge_allowed=false`。

它不扫描业务源码、不执行项目测试、不调用模型、不创建分支或 PR，也不执行 merge/CD。开发者仍需确认需求范围、检查代码差异，并通过 GitHub 的正常保护规则完成合并。

## 效率与面试价值

该能力把原本分散的体检、任务合同、PR 检查和二次核验变成一个入口，但当前没有真实人工节省比例。请用 `record` 记录同范围任务的人工分钟、质量、返工和证据，再由 `benchmark` 计算观察结果。面试时可以演示状态机、工具调用、上下文边界、失败短路、幂等产物和人机责任分界。
