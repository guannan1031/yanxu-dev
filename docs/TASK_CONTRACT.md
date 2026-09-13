# v0.4：需求任务合同

`task` 是开发前的本地入口，把一条需求和项目中真正影响交付的上下文固定下来。它读取有限白名单：`AGENTS.md`、`README.md`、贡献指南、构建配置、测试配置、`.github/workflows` 和少量 `docs/*.md`；不扫描业务源码，也不执行项目代码。

## 使用

```bash
python -m yanxu task "Add a safe pagination endpoint" \
  --repo /path/to/repo --output runs/tasks
```

输出：

- `task.json`：带 schema、上下文文件 SHA-256、建议测试命令、验收条件和下一步动作；
- `task.md`：可直接放入 issue、PR 描述或交给 Agent 的人类可读版本。

## 为什么它属于开发中台

任务合同把需求入口和后续交付证据连起来：开发者先确认范围与验收，再实现分支；`review` 采集 PR/CI 事实，`verify` 检查报告是否过期，`prepare-fix` 准备受限补丁，`test-fix` 在记录 commit 的完整归档中验证修复。合同本身不批准 merge，也不代替人工确认。

## 边界

上下文文件单文件最多 20 KB、总计最多 80 KB、最多 12 个文件，并做最佳努力脱敏。输出路径默认为本地 `runs/`，不会写 GitHub、创建分支、修改源码或保存凭据。自动生成的验收条件是起点，真实团队仍需补充业务指标、接口契约和发布回滚条件。

本版本本地测试共 47 项通过；提效百分比仍需用同类真实需求做基线对照后计算。
