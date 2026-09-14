# Yanxu Team Workspace Alpha

`team` 是一个文件化、可私有化运行的团队工作空间。负责人只登记已授权项目的 `runs/` 和可选测量文件；生成看板时不会读取业务源码、上传数据、创建 PR、merge 或部署。

```bash
# 1. 初始化一个团队工作空间
python -m yanxu team init --name "Platform Team" --output .yanxu/team-workspace.json

# 2. 为每个已授权项目登记本地交付产物
python -m yanxu team add-project .yanxu/team-workspace.json \
  --id orders-service --runs /path/to/orders-service/runs \
  --measurements /path/to/orders-service/runs/observed.json

# 3. 导出跨项目团队交付看板
python -m yanxu team board .yanxu/team-workspace.json --output runs/team-board.html
```

## 输入、输出与验收

| 项目 | 说明 |
| --- | --- |
| 输入 | 团队工作空间 JSON；每个项目显式登记的运行目录和可选测量文件 |
| 输出 | `team-board.html`、`team-board.json` |
| 显示 | 项目可用性、运行数、隔离测试、待人工复核、项目级测量状态 |
| 不做 | 不聚合不同项目的提效百分比；不读取源码；不上传；不写 GitHub |

项目目录失效时，看板将该项目标为 `UNAVAILABLE`，但保留其他项目的结果。

## 商业定位

这是“私有团队版 Alpha”，适合单个客户在自己的开发环境内完成多个仓库的接入验证。付费试点可包含规则包配置、CI/PR 流程接入、交付看板和维护服务。正式团队账号、RBAC、GitHub/GitLab OAuth、Webhook、远程 Runner、多租户隔离和托管计费尚未实现，后续应在真实试点证明需求后建设。
