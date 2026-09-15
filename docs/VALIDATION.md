# 功能验证与简历证据

日期：2026-09-15。用途：说明实际完成了什么，以及哪些效果尚未测量。

## v0.15 当前状态（2026-09-14）

当前公开工具已覆盖项目体检、任务合同、受控代码生成、隔离测试、PR/CI 证据复核、受控 Draft PR、真实测量、团队 Policy、私有团队看板、脱敏试点证据包和本地 GitHub 多项目只读同步。`team sync-github` 读取明确登记的 PR 并持久化 commit、CI、版本绑定和判断，不保存 diff、PR 正文或日志。

本地完整回归共 91 项测试通过。另以 CLI 对真实 [PR #14](https://github.com/guannan1031/yanxu-dev/pull/14) 完成团队同步：记录为已合并、CI `PASSING`，PR 判断为 `BLOCKED`，因为已合并 PR 不再是开放待审对象；持久化快照未包含 diff 或 PR 正文。远端 Linux/Windows CI 以对应发布提交的 GitHub Actions 结果为准。

## v0.16 私有服务验收（2026-09-14）

- PostgreSQL 集成测试覆盖两家组织隔离、同名工作空间、owner/viewer 写权限、令牌撤销、快照幂等和审计；服务相关 6 项测试通过，完整发现共 99 项测试通过，其中无数据库环境时跳过 2 项集成测试。
- Docker Compose 从空卷启动，`/healthz` 返回数据库可用；通过 CLI 显式发布仓库内真实 GitHub 同步演示快照，首次创建后再次发布返回同一快照和 `created=false`。
- 删除测试数据库卷后，从 `pg_dump` 备份恢复并重启服务，取回原快照 ID `e4775054-5173-4012-94f4-e98ef70fdc49` 与指纹 `ccd98c89fa4aef540b08622b4a5a652c86c565d445eec0082f0fe9744b0821f3`，其中 CI 为 `PASSING`。
- 数据库检查确认明文引导令牌匹配数为 0；持久化项目不含输入中额外添加的 `diff` 或 `body` 字段。

这些是本机合成组织和公开仓库快照的产品验收证据，不是外部客户生产验收、付费或效率收益证据。独立远端环境证据以本版本 GitHub Actions 的 PostgreSQL job 结果为准。

## v0.16.1 GitHub 事件验收（2026-09-14）

- 完整测试共 105 项通过；新增测试使用 GitHub 官方 HMAC-SHA256 向量，并覆盖错误签名、字段白名单、delivery 去重、组织隔离、Worker 和安装撤权。
- Docker Compose 同时运行 `app`、`db`、`worker`。合成 PR delivery 首先返回 `PENDING`，后台 Worker 随后写为 `COMPLETED`，`attempt_count=1`。
- Docker 实测 delivery `smoke-b7d68ae9e6764388b37109ad650a5a5a` 绑定公开仓库 `guannan1031/yanxu-dev` 和 PR #16；持久化事实不含输入中的 PR 标题或正文。
- GitHub installation 的 `suspend/deleted` 事件会立即改为 `REVOKED`；后续事件标为 `IGNORED_REVOKED`，不进入 Worker。

上述 delivery 是本机按 GitHub 协议构造的合成请求。真实公网 GitHub App 注册、HTTPS Webhook 送达和客户仓库验收仍待试点配置，不能写成已线上联调。

## v0.17.0 私有团队工作台验收（2026-09-14）

- 完整测试共 109 项通过。新增覆盖浏览器会话哈希、HttpOnly/SameSite Cookie、退出撤销、底层 API Token 撤销联动、跨组织页面与导出隔离、动态文本转义、Cookie 安全配置校验和审计指纹复算。
- Playwright 在真实 Chromium 中完成 `/login` 输入组织 Token、跳转 `/app`、读取工作空间/PR/CI/Webhook/审计状态并退出回到登录页。
- 浏览器验收使用本地合成组织 `pilot-demo`、公开仓库 `guannan1031/yanxu-dev` 和合成 `check_run` delivery；工作台显示 1 个工作空间、1 个项目、CI `PASSING`、0 个待处理事件和 1 个快照。
- 审计导出只返回当前组织事件；响应头与 JSON 的 `fingerprint` 一致，并可按文档规则重新计算。页面与导出均不含源码、diff、PR 正文、日志或明文 Token。

截图见[私有团队工作台](PILOT_DASHBOARD.md)。这是本机私有服务的产品验收，不是外部团队 UAT、付费或效率收益证据。

## v0.17.1 实际成本与试点包验收（2026-09-15）

- 完整测试共 110 项通过。新增覆盖十进制金额精确解析、6 位小数上限、owner/viewer 写权限、跨组织隔离、分币种/类型汇总、成本审计和 ZIP 合同。
- `12.50` 在 PostgreSQL 保存为 `12500000` 微单位并规范化显示为 `12.5`；不同币种保持独立，没有隐含汇率换算或 Token 计价。
- Playwright 在真实 Chromium 中通过页面录入 `CNY 7.250001` 的合成支持成本；刷新后成本数和审计同步增加，控制台 0 错误。
- 浏览器成功下载试点 ZIP。压缩包只含 `summary.json`、`costs.json`、`audit.json` 和 `manifest.json`；测试逐项复算文件哈希与 manifest 指纹。
- Docker Compose 用 v0.17.1 镜像重建 app/worker 后健康检查通过；写入 `USD 4.125` 合成基础设施成本，重启 app/worker 后仍取回 `4125000` 微单位和同一证据引用。
- manifest 明确写入 `efficiency_claim_status=NOT_MEASURED`，不会因已有成本或 CI 通过而自动生成提效比例。

截图见[成本与试点证据包](PILOT_COSTS.md)。这些成本、组织和事件均为本地合成验收数据，不是客户账单、生产用量、外部 UAT 或商业收入。

公开证据：[v0.17.1 Release](https://github.com/guannan1031/yanxu-dev/releases/tag/v0.17.1) · [主分支 CI](https://github.com/guannan1031/yanxu-dev/actions/runs/34904575884) · [GitHub Pages](https://github.com/guannan1031/yanxu-dev/actions/runs/34904574988)。Release JSON SHA-256 为 `f1d3c603289b08177b891d1fcef626768b345bad74d557c2ac22ce4ff3666b6d`，截图 SHA-256 为 `0ff5f7d486d324456b0ef51d65103f37def1d39e0ef2625e2df246e39cc6aea1`。

## v0.18.0 备份与恢复验收（2026-09-15）

- `ops backup` 通过 Docker Compose 在 PostgreSQL 容器内生成自定义归档，复制到明确的本地路径后计算 SHA-256 并生成 manifest；已存在输出时拒绝覆盖。
- `ops restore` 在任何停服动作前核对文件名、字节数、SHA-256 和 `pg_restore --list`；缺少 `--confirm-restore` 或文件被改动时拒绝。
- 恢复使用 `--clean --if-exists --single-transaction`。自动化测试模拟恢复失败并验证 app/worker 仍被重新启动。
- 真实 Docker 合成环境生成 25,651 字节归档，SHA-256 为 `47b43a6e035d57db65d04eaac520758b3923b6f26f98c858b9ca56c28c6d8d99`。备份后成本记录由 1 条增至 2 条；恢复并重启后回到 1 条，原金额仍为 `4125000` 微单位。

该验证覆盖单机 Docker Compose 的逻辑备份和恢复，不构成外部客户灾备演练、RTO/RPO 或跨云恢复承诺。

公开证据：[v0.18.0 Release](https://github.com/guannan1031/yanxu-dev/releases/tag/v0.18.0) · [主分支 CI](https://github.com/guannan1031/yanxu-dev/actions/runs/34905649576) · [GitHub Pages](https://github.com/guannan1031/yanxu-dev/actions/runs/34905648459)。Release 验证 JSON SHA-256 为 `a4c93a4f5f4abfd57e7971db3f26f1aaffe1d7a35120c3140b2fe847403bd63c`；私有数据库 dump 未上传公开 Release。

## v0.19.0 私有试点初始化验收（2026-09-15）

- `pilot init` 生成三个 43 字符随机私密值，输出不含这些值；`.env` 在 POSIX 本机权限为 `0600`，再次初始化拒绝覆盖。
- `pilot doctor` 的 11 项检查覆盖文件、必填键、占位符、私密值长度、slug、端口、Cookie、Docker、Compose 和配置展开；真实检查全部 `PASS`，报告不含任一私密值。
- 使用生成的 `.env` 从空 PostgreSQL 卷构建 app/db/worker。`/healthz` 返回数据库可用，OpenAPI 版本为 `0.19.0`。
- 生成的组织 Token 成功换取 HttpOnly 浏览器会话并进入 `onboarding-demo` 工作台；登录响应和页面未回显 Token。验证后删除合成容器、网络、卷和本地 `.env`。

公开证据：[v0.19.0 Release](https://github.com/guannan1031/yanxu-dev/releases/tag/v0.19.0) · [主分支 CI](https://github.com/guannan1031/yanxu-dev/actions/runs/34906726771) · [GitHub Pages](https://github.com/guannan1031/yanxu-dev/actions/runs/34906726183)。Release 验证 JSON SHA-256 为 `19966fef3826f797e907167eae772cad31b603dab5636563492d42c7a251c7fb`。

该验证说明本机冷启动路径可运行，不代表客户的 HTTPS、网络策略、GitHub App 权限或外部 UAT 已通过。

## v0.20.0 试点验收与支持证据（2026-09-15）

- PostgreSQL 增加组织隔离的验收项和支持记录；owner 可写，viewer 只读，另一个组织读取为空。
- `PASS`/`FAIL` 缺少证据引用时拒绝；客户确认只允许用于 `PASS`。汇总状态区分 `INTERNAL_PASS` 与 `CUSTOMER_CONFIRMATION_RECORDED`。
- 真实 Chromium 完成验收标准新增、状态更新、客户确认和 45 分钟接入支持记录；页面显示动态内容，控制台 0 错误。该流程发现并修复了验收更新表单的绑定时序问题，回归断言覆盖事件委托。
- 浏览器下载的试点 ZIP 增加 `acceptance.json` 与 `support.json`，6 个文件的 SHA-256 均可从 manifest 复算；manifest 继续标记提效 `NOT_MEASURED`。
- PostgreSQL 集成环境执行 119 项测试全部通过；v0.20.0 wheel 的版本和服务模块检查通过。页面截图 SHA-256 为 `3762e9ac701f8b37b153d8cf5e8e87cd798e99025814c751b9970c5b7b789774`。
- 外部客户身份、签字、付费和真实提效仍未验证。

公开证据：[v0.20.0 Release](https://github.com/guannan1031/yanxu-dev/releases/tag/v0.20.0) · [主分支 CI](https://github.com/guannan1031/yanxu-dev/actions/runs/34908312749) · [GitHub Pages](https://github.com/guannan1031/yanxu-dev/actions/runs/34908312232)。Release 验证 JSON SHA-256 为 `73e1ef1fbf58d586a30a202ae9790e77dd0fe7edc1c886b506e751f49d521a43`，wheel 为 `1caea10b6142d17395bab5801d20534ca6d4bc400239f239cc5ee6cc893165cb`。

## v1.0.0 可打印验收报告（候选版，2026-09-15）

- `/v1/pilot/report` 从当前组织的验收项、客户确认记录、支持分钟和实际成本生成打印页面；同一份快照规范化后计算 SHA-256，并同时放入响应头和报告正文。
- viewer 可以读取报告但不能修改试点记录；另一个组织看不到当前组织证据。动态标准与证据引用经过 HTML 转义，响应保持 CSP 和 `no-store`。
- PostgreSQL 集成环境执行 119 项测试全部通过；`yanxu_dev-1.0.0-py3-none-any.whl` 的版本和服务模块检查通过。
- Docker Compose 从空卷启动 v1.0.0 app/db/worker；真实 Chromium 完成 Token 登录、进入工作台、录入合成验收/支持/成本并打开打印报告，控制台 0 错误。
- 打印报告截图 SHA-256 为 `5cc093d6803f7663ba8f990cdf0a947dfd993d92a44bdf70977339f7b4a45f4a`。截图中的组织、证据、60 分钟和 CNY 28.5 均为合成验收数据。

发布前状态仍是候选版。远端 Windows/Linux CI、GitHub Release、发布 wheel 哈希和主分支 Pages 链接必须在对应 PR 合并后补齐。该报告不是电子签名、付款证明、外部客户验收或提效百分比证明。

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

合并动作由开发者执行，**不是 Yanxu 产品的自动合并功能**。私有 API 只保存用户显式发布的标准化证据；没有进行生产部署或业务验收。

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

> **研序 Yanxu Dev｜开源 AI Coding 交付治理平台（个人项目，v1.0.0 候选版）**
> 设计并实现需求合同、团队 Policy、受控代码生成、隔离测试、PR/CI 证据核验和 Draft PR 交付闭环；实现 FastAPI + PostgreSQL 私有团队服务、组织隔离、owner/viewer、审计、GitHub Webhook 队列与 Docker 部署。浏览器工作台记录验收标准、客户确认、支持投入和分币种成本，导出带 SHA-256 manifest 的试点包与可打印验收报告；跨平台命令支持脱敏安装预检、数据库备份、篡改校验和单事务恢复。系统明确区分内部通过、客户确认记录和外部签字；提效未完成配对测量时保持 `NOT_MEASURED`。源码：https://github.com/guannan1031/yanxu-dev

本项目使用 AI 辅助开发并复用开源执行器。个人贡献以能够讲解、修改和验证的内容为准；不要写成自研大模型、已经落地企业平台或有未经测量的提效百分比。

面试可重点演示：为什么 merge 前后都运行 CI；如何区分规则事实与模型建议；如何绑定 head/base 与检查；为什么新提交不能沿用旧报告；失败时如何保留证据。
