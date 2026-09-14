# 功能验证与简历证据

日期：2026-09-13。用途：说明实际完成了什么，以及哪些效果尚未测量。

## v0.14 当前状态（2026-09-14）

当前公开工具已覆盖项目体检、任务合同、受控代码生成、隔离测试、PR/CI 证据复核、受控 Draft PR、真实测量、团队 Policy、私有团队看板和脱敏试点证据包。`team export` 只导出标准化看板、Policy 指纹、manifest 和文件哈希，不复制业务源码、原始运行产物、凭据或登记的本地绝对路径。

本地完整回归共 87 项测试通过；另以 CLI 完成 `team init → policy → add-project → export` 冒烟验证，并检查 ZIP 内容、Policy 状态、本地路径与原始内容排除。远端 Linux/Windows CI 以对应发布提交的 GitHub Actions 结果为准。

## 真实集成记录

公开合成示例：[PR #1](https://github.com/guannan1031/yanxu-dev/pull/1)。这是预先设计的分页回归，不是未知生产缺陷、盲测或客户任务。

| 环节 | 结果 | 依据 |
|---|---|---|
| 初始 main CI | Python 3.11/3.13 通过 | [运行 34753687601](https://github.com/guannan1031/yanxu-dev/actions/runs/34753687601) |
| 制造独立测试可发现的缺陷 | `start = page * size` 违反一基页码约定 | 提交 `74e0c96166d0adaca4aeff0705a25be7439bd1ac` |
| 合并前 CI 真失败 | 3.13 的两个分页断言失败；3.11 被矩阵 fail-fast 取消 | [运行 34753701587](https://github.com/guannan1031/yanxu-dev/actions/runs/34753701587) |
| 真模型诊断 | Codex 指出了 off-by-one 问题，引用两个失败断言并建议一行修复 | [完整 evidence](demo-evidence.json)、[历史 HTML 报告](index.html) |
| 修复前重新核对 | `UNCHANGED` | [核对记录](demo-verify-before.json) |
| 由开发者应用建议 | 恢复 `(page - 1) * size`；测试文件未更改 | 提交 `de182422b5ee5ed64fc029625dbaeb3b9f8740b1` |
| 新提交使旧报告过期 | `STALE`，`changes=[head_sha]`，CLI 退出码 2 | [核对记录](demo-verify-after.json) |
| 修复后的 PR CI | Python 3.11/3.13 通过 | [运行 34753802102](https://github.com/guannan1031/yanxu-dev/actions/runs/34753802102) |
| 开发者受控合并 | PR 实际合并；合并请求绑定当时 head SHA | merge commit `2a1236f9533ba4c5ff1290865617589ffc255ab0` |
| 合并后 main CI | Python 3.11/3.13 通过 | [运行 34753866731](https://github.com/guannan1031/yanxu-dev/actions/runs/34753866731) |

合并动作由开发者执行，**不是 Yanxu 产品的自动合并功能**。Yanxu 当前只读。没有进行生产部署或业务验收。

## 单次观测数据

- 样本：1 个公开合成 PR，2 个失败分页断言，1 次 AI 诊断。
- 从开始收集到报告内容生成：36.982 秒；其中 Codex 调用 29.032 秒。
- CLI：`codex-cli 0.137.0`，使用其默认模型，未独立解析实际模型标识。
- 用量事件：input 26,575 tokens，output 719 tokens，另报 reasoning output 56 tokens；不假定这些字段可以相加计价。金额未核算。
- 修复后仅采集事实、不调用 AI 的报告：6.544 秒。
- 本地回归：24 个测试通过，覆盖过期、缺失检查、取消、审批撤回、输入校验、AI 失败、HTML 转义和分页样例。

机器时间可复核；**人工净节省、客户成功率、商业 ROI 和团队开发提效百分比均未测得**。一个已知缺陷演示不构成通用修复能力证明。

## 下一轮效率测量

选择至少 6 对难度相近的 PR 任务，覆盖失败诊断、审查整理、审批后变更三类。基线使用同等模型的现有 Coding 工具与 GitHub 原生页面；实验组使用研序。交替执行顺序，避免先知道答案的一组天然更快。

每次分别记录：主动人工分钟、机器等待分钟、总历时、报告中的事实错误、漏报、修正分钟、最后验收结果。失败和接管均计入样本；平台维护成本单列。

`人工时间减少率 = (基线人工分钟 - 工具组人工分钟) / 基线人工分钟 × 100%`

只在质量不恶化且原始记录完整时报告这一局部指标；不能自动称为整个研发流程提升同样比例。样本小则明确为探索性观察。

## 今天可使用的简历表述

> **研序 Yanxu Dev｜开源 AI PR/CI 诊断工具（个人项目，v0.1）**  
> 设计并实现基于 GitHub API 与 Codex CLI 的研发交付审查工具，完成 PR 差异、CI 状态及失败日志的上下文汇总、结构化 AI 诊断、HTML/JSON 报告与提交版本过期核验；通过公开合成 PR 验证“CI 失败—AI 定位—人工修复—PR CI 通过—合并后 CI 通过”流程，单次报告生成约 37 秒。源码：https://github.com/guannan1031/yanxu-dev

本项目使用 AI 辅助开发并复用开源执行器。个人贡献以能够讲解、修改和验证的内容为准；不要写成自研大模型、已经落地企业平台或有未经测量的提效百分比。

面试可重点演示：为什么 merge 前后都运行 CI；如何区分规则事实与模型建议；如何绑定 head/base 与检查；为什么新提交不能沿用旧报告；失败时如何保留证据。
