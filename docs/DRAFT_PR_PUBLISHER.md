# v0.9：受控 Draft PR 发布器

`draft-pr` 面向已有 GitHub、CI 和测试的小型研发团队。它把发布前容易遗漏的分支、提交、远端、base 新鲜度和改动范围检查集中为一个入口，并只在显式确认后推送当前功能分支、创建 Draft PR。

## 先生成计划

```bash
python -m yanxu draft-pr --repo . --github-repo owner/repo \
  --base main --head feat/example \
  --allow-path yanxu/example.py --allow-path tests/test_example.py \
  --title "feat: add example" --body-file /tmp/pr-body.md
```

默认输出 `READY_TO_CREATE_DRAFT` 以及 JSON、Markdown、HTML；不会修改远端。工具要求工作区干净、当前分支等于 head、功能分支领先 base、本地 base 与 GitHub base 一致、origin 与目标仓库一致、没有同 head 的开放 PR，并且实际改动文件与 `--allow-path` 完全相等。

## 显式发布

人工检查计划、标题、正文和文件范围后，在相同命令末尾增加：

```bash
--confirm-create
```

工具使用无强推的 `git push -u origin <head>`，再次确认 HEAD 未变化，再通过 GitHub CLI 创建 Draft PR。推送成功但 PR 创建失败时，报告保留 `remote_branch_pushed=true`；push 本身报错时记录 `VERIFY_REQUIRED_AFTER_PUSH_ERROR`，要求人工核对远端，不把不确定状态写成未修改或全部完成。

## 工具边界与验收

输入包括授权的本地 demo 仓库、GitHub repo、base/head、精确路径白名单、标题和正文文件。工具调用仅限本地 Git 检查、GitHub 只读核对，以及显式确认后的分支推送和 Draft PR 创建。它不生成或修改代码、不运行任意项目命令、不创建正式 PR、不 Review、不 merge、不部署。

验收标准：默认执行无远端修改；脏工作区、错误远端、过期 base、重复 PR、敏感路径或范围漂移均拒绝；确认执行只创建 Draft PR；所有结果保留本地报告，`auto_merge_allowed` 始终为 false。

## 效率与面试价值

该能力减少开发者在发布分支前逐项核对 GitHub 状态和整理 PR 的操作，但当前没有真实节省比例。用 `record` 记录人工时间、质量、返工和证据，再由 `benchmark` 做同范围比较。面试可演示 dry-run、显式副作用门、提交绑定、范围白名单、部分失败恢复和人机职责边界。
