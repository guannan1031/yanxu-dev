# v0.3：从诊断建议到隔离验证

日期：2026-09-13。

新增 `prepare-fix`：读取保存的 AI 建议，默认重新核对当前 GitHub PR，然后在新建的最小副本中应用补丁。原工作区的源码和未提交改动不受影响。

## 工作流与结果

1. 校验快照指纹、上下文完整性和 AI 输出状态。
2. 校验补丁所有头部、明确允许的文件清单和文件类型。
3. 在线模式重新采集 GitHub；提交、需求、CI 或审批变化，以及 PR 关闭，均停止准备。
4. 从记录的 commit 读取目标文件，不读取工作区的未提交版本。
5. 在新目录中执行 `git apply --check`，再应用补丁，核对实际改动路径。
6. 输出 `verified.diff`、最小文件副本、`manifest.json`，标记 `PREPARED_NOT_TESTED`。

外部工具仍复用 Git 和现有 Codex；没有复制一套 Coding 执行器。此命令不运行仓库代码，不创建远端分支，不批准或合并 PR。新目录是补丁准备区，不是执行沙箱。

## 使用

```bash
python -m yanxu prepare-fix runs/RUN_ID/evidence.json \
  --checkout /path/to/authorized/repo \
  --allow-path src/example.py
```

要处理多个文件，重复 `--allow-path`。本版支持最多 10 个已有的 UTF-8/LF 普通文本文件，不支持新建、删除、重命名、二进制文件、符号链接、子模块；隐藏文件和独立测试文件禁止修改。全局 Git 配置、模板 hooks 和 fsmonitor 不参与副本处理。

## 无需模型和网络的历史回放

仓库携带 v0.1 的实际 AI 输出，可以重放已有公开案例：

```bash
python -m yanxu prepare-fix docs/demo-evidence.json \
  --checkout . --allow-path sample/pagination.py --replay
```

要求本地 Git 包含演示 commit；普通完整 clone 即可。浅克隆如果缺失该 commit，应先取得相应历史。`--replay` 跳过在线核验，结果明确标记 `historical_replay`，不能用于当前 PR 的合并判断，也不会触发 GitHub 失败邮件。

实际回放：记录中的 `start = page * size` 在独立副本中变为 `start = (page - 1) * size`。随后开发者审查该六行合成代码，并用保持原内容的四个分页回归测试验证通过。**这是开发者对合成示例的独立验证，不是产品已经有通用自动测试执行器。**

- [准备清单](patch-replay-manifest.json)
- [开发者独立验证](patch-replay-tests.json)
- [准备后的标准 diff](patch-replay.diff)

本版本地 37 项测试通过，新增范围覆盖了工作区保护、在线过期拦截、关闭 PR、回放不联网、路径越界、头部不一致、带制表符时间戳的文件头、模式变更、符号链接、二进制/行尾转换拒绝和失败清单。

本次还用研序诊断了真实功能 PR #3，并尝试在线准备其 AI 建议。该建议省略了 unified diff 的 hunk 行号，工具在 `git apply --check` 阶段正确拒绝；原工作区未被覆盖。开发者随后补充带时间戳文件头的支持与实际应用回归测试。模型建议与有效补丁仍须分开验证。

## CI 邮件说明

历史运行 `34753701587` 是 v0.1 功能演示主动引入缺陷的失败；已修复并合并。旧邮件不会因为修复而撤回。现在故障回归用断言验证“正确阻断”，测试本身通过，不重复制造远端红灯。当前状态应看最新提交的 CI，而不是旧邮件或历史报告中的 `BLOCKED`。

## 可用于简历的新增能力

> 在 PR/CI 诊断基础上实现受限补丁准备：按已记录提交构建独立最小副本，校验文件清单与补丁适用性，保护原工作区及独立测试，并记录补丁哈希和版本证据。

## test-fix：在修复副本中验证

`test-fix` 将记录的 commit 归档到临时完整工作区，先校验并应用同一份 AI 建议补丁，再执行开发者通过 `--command` 明确给出的测试命令。命令以 `shell=False` 启动，不继承 GitHub/Codex 凭据，且有超时、输出长度和可执行文件白名单；这属于工程隔离，不是强化安全沙箱。结果写入 `manifest.json` 与 `test-output.log`：

- `COMPLETED` / `PASSED`：补丁后的临时归档测试通过；
- `FAILED_TESTS` / `FAILED`：测试进程返回非零；
- `TIMED_OUT` / `TIMEOUT`：超过显式超时，进程组被终止。

```bash
python -m yanxu test-fix --checkout /path/to/authorized/repo \
  --allow-path sample/pagination.py docs/demo-evidence.json \
  --replay --command python3.11 -m unittest discover -s tests -v
```

历史回放结果：同一公开证据、同一记录 commit 和建议补丁，在完整归档中运行 21 项测试并通过；原 checkout 未修改，远端未写入。`--replay` 只用于复现历史，不代表当前 PR 仍然新鲜。

本版本本地 44 项测试通过，覆盖测试通过、测试失败、超时、命令校验、在线过期拦截、回放不联网和原工作区保护。尚未宣称固定研发提效百分比；需用同类真实任务记录基线后再计算。
