# v0.6：真实观察记录器

`record` 将一个真实任务的基线侧或研序侧追加到 observed 数据集。它不自动推断人工时间：开发者填入自己实际记录的主动人工分钟，并提供可复核证据引用。

## 输入、处理和输出

- 输入：固定观察范围、任务 ID/类型、`baseline` 或 `yanxu`、人工分钟、质量结果、返工次数、证据引用、范围是否一致；
- 处理：校验数据集 schema 和范围，按任务 ID 查找配对记录，脱敏文本，原子写入；
- 输出：数据集路径、刚记录的一侧、配对是否完成、完整配对数；
- 工具边界：只写指定本地 JSON，不读取证据文件、不上传代码、不调用模型或 GitHub；
- 验收：同一任务同一侧不能重复写，任务类型和范围一致性不能漂移，缺证据不能进入真实评测。

## 使用

先记录人工基线：

```bash
python -m yanxu record runs/observed.json \
  --scope "Python bug fixes / 2026-09" \
  --task-id BUG-123 --task-type bugfix --variant baseline \
  --human-minutes 30 --quality-passed --rework-count 1 \
  --evidence "PR-123/baseline-notes.md" --same-scope
```

再记录研序流程：

```bash
python -m yanxu record runs/observed.json \
  --scope "Python bug fixes / 2026-09" \
  --task-id BUG-123 --task-type bugfix --variant yanxu \
  --human-minutes 18 --quality-passed --rework-count 0 \
  --evidence "PR-123/yanxu-report.json" --same-scope
```

最后生成报告：

```bash
python -m yanxu benchmark runs/observed.json --output runs/observed-report
```

如果两侧范围不同，使用 `--different-scope`，该任务会保留但不进入时间减少率。质量失败使用 `--quality-failed`，同样保留并显示排除原因。错误记录不会自动覆盖；应审查并显式修正数据集，保留修改依据。

## 效率指标与面试价值

这条链路把“提升研发效率”从口号变为可追溯数据：任务 ID 对应需求，证据引用对应交付材料，质量门防止用低质量结果换速度。至少积累 5 个同范围有效配对任务后，才可在简历中描述该范围内观察到的人工时间变化，并同时报告返工变化。
