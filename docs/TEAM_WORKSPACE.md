# Yanxu Team Workspace Alpha

`team` 是一个文件化、可私有化运行的团队工作空间。负责人只登记已授权项目的 `runs/`、可选测量文件和明确的 GitHub PR。生成看板不会上传数据、创建 PR、merge 或部署。

```bash
# 1. 初始化一个团队工作空间
python -m yanxu team init --name "Platform Team" --output .yanxu/team-workspace.json

# 2. 为每个已授权项目登记本地交付产物
python -m yanxu team add-project .yanxu/team-workspace.json \
  --id orders-service --runs /path/to/orders-service/runs \
  --measurements /path/to/orders-service/runs/observed.json \
  --policy /path/to/orders-service/.yanxu/team-policy.json \
  --github-repo owner/orders-service --pr 123

# 3. 新任务开始时更新该项目当前跟踪的 PR
python -m yanxu team set-github .yanxu/team-workspace.json \
  --id orders-service --github-repo owner/orders-service --pr 124

# 4. 使用当前 gh 登录只读同步全部已登记 PR，并生成绑定 commit 的团队看板
python -m yanxu team sync-github .yanxu/team-workspace.json \
  --output runs/team-github

# 5. 不访问 GitHub，只按本地运行产物导出团队看板
python -m yanxu team board .yanxu/team-workspace.json --output runs/team-board.html

# 6. 生成可交给试点客户离线验收的脱敏证据包
python -m yanxu team export .yanxu/team-workspace.json \
  --github-snapshot runs/team-github/TIMESTAMP/team-github.json \
  --output runs/yanxu-pilot-evidence.zip
```

## 输入、输出与验收

| 项目 | 说明 |
| --- | --- |
| 输入 | 团队工作空间 JSON；显式登记的运行目录、Policy、测量文件和 GitHub PR |
| GitHub 处理 | 临时读取 PR、diff、check/status 和 review 以完成完整性与版本绑定；只持久化标准化 PR、commit、CI 和判断字段 |
| 输出 | 每次同步的 `team-github.json`、`team-board.html`、`team-board.json`；可选脱敏 ZIP 证据包 |
| 显示 | 项目可用性、Policy 状态和指纹、运行数、隔离测试、GitHub 同步、CI、待人工复核和项目级测量状态 |
| 不做 | 不保存 diff、PR 正文或日志；不聚合不同项目提效百分比；不上传；不评论、审批、merge 或部署 |

项目目录失效时，看板将该项目标为 `UNAVAILABLE`，但保留其他项目的结果。

证据包只包含标准化后的团队看板、可选 GitHub 快照、manifest、文件哈希和阅读说明，不复制原始运行产物、业务源码、PR diff/正文/日志、凭据或工作空间中登记的本地绝对路径。已有 ZIP 默认拒绝覆盖，便于保留每次验收的独立证据。

`sync-github` 使用用户当前已登录的 GitHub CLI，每次创建独立时间戳目录。所有已配置项目同步成功时状态为 `COMPLETED`；部分失败为 `PARTIAL`，全部失败为 `FAILED`。失败项目保留错误，其他项目结果仍写入。当前版本是本地拉取模式，尚未实现 GitHub App、Webhook、后台定时同步或仓库撤权事件处理。

## 商业定位

这是“私有团队版 Alpha”，适合单个客户在自己的开发环境内完成多个仓库的接入验证。付费试点可包含规则包配置、CI/PR 流程接入、交付看板和维护服务。正式团队账号、RBAC、GitHub/GitLab OAuth、Webhook、远程 Runner、多租户隔离和托管计费尚未实现，后续应在真实试点证明需求后建设。
