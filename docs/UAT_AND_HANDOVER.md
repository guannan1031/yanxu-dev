# v1.0 客户 UAT 与交接清单

本清单用于研序私有化试点的技术验收和交接。客户签字在导出的打印报告或双方合同系统中完成；平台只记录证据引用和“客户确认已录入”，不提供电子签名或身份核验。

## 验收角色和环境

- 客户负责人：确认试点范围、验收标准、证据和最终结论。
- 客户开发者 / Reviewer：运行任务、检查补丁、测试和 PR/CI。
- 交付负责人：部署、配置、培训、记录支持投入并移交材料。
- 环境：客户本地或内网 Docker 主机、PostgreSQL、GitHub 仓库和约定的 Coding Agent。

不要把生产 Token、Webhook secret、Cookie、数据库密码、客户源码或签字文件放入 GitHub、截图和公开 Release。

## UAT 步骤

| 编号 | 验收动作 | 通过标准 | 建议证据引用 |
| --- | --- | --- | --- |
| UAT-01 | 执行 `pilot init` 和 `pilot doctor` | 不覆盖已有配置；所有预检项通过；输出不含私密值 | 脱敏 doctor JSON |
| UAT-02 | 启动 Docker Compose 并访问 `/healthz` | app、db、worker 正常，数据库可用 | 环境验收记录 |
| UAT-03 | 用 owner/viewer 分别登录 | owner 可写，viewer 只读，撤权后会话失效 | 权限测试记录 |
| UAT-04 | 接入一个批准的仓库或合成事件 | 签名校验、delivery 去重、组织隔离和撤权有效 | Webhook delivery id |
| UAT-05 | 执行一项受控开发任务 | 只修改 Policy 允许路径，批准测试命令在隔离副本运行 | task/workflow evidence |
| UAT-06 | 核对 merge 前 CI | PR head 对应检查全部满足后才进入人工合并 | PR 和 commit 链接 |
| UAT-07 | 核对 merge 后 CI | main 的 merge commit 重新运行 CI；失败进入处理流程 | main CI 链接 |
| UAT-08 | 录入验收、支持和成本 | PASS/FAIL 有证据；客户确认只用于 PASS；金额分币种 | 工单或会议纪要引用 |
| UAT-09 | 导出 ZIP 和打印报告 | manifest 哈希可复算；报告指纹与响应头一致 | 导出文件哈希 |
| UAT-10 | 备份并恢复 | 校验通过后单事务恢复，服务重启且数据回到备份时点 | 恢复演练记录 |

## 交接材料

- 当前 Release、wheel 哈希、部署主机和服务端口。
- 组织、角色、接入仓库和 Policy 指纹清单。
- `.env` 的保管人和轮换流程，只记录责任人，不复制私密值。
- GitHub App 权限、Webhook 地址和撤权步骤。
- 备份位置、manifest、恢复演练记录和升级回退步骤。
- 试点 ZIP、可打印验收报告、外部签字文件位置和支持工单清单。
- 尚未完成或标记 `NOT_MEASURED` 的验收与效率项目。

## 最终确认

在工作台由 owner 将有证据的验收项更新为 PASS 或 FAIL。客户确认后，可记录相应的证据引用并生成 `/v1/pilot/report`。下载或打印报告后，双方按合同流程签署；签署文件保存在双方批准的位置，平台不上传签名原件。

只有全部强制验收项通过，且双方外部确认文件完成后，才可表述为“客户 UAT 通过”。本仓库自身的合成验收只能证明产品流程可运行。

