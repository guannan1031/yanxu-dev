# 备份、恢复与升级

Yanxu Dev v0.18.0 为私有团队试点提供跨 Windows、macOS 和 Linux 的 Docker Compose 数据保护流程。目标用户是客户侧运维人员或 Yanxu 试点交付负责人；它保护 PostgreSQL 中的组织、令牌哈希、会话、工作空间、PR/CI 快照、GitHub delivery、成本和审计记录。

## 操作边界

| 项目 | 规则 |
|---|---|
| 输入 | 含 `compose.yaml` 的目录、明确的本地备份路径；恢复时必须同时提供同名 `.json` manifest |
| 处理 | 通过参数数组调用 Docker Compose，不经过 shell；备份后计算本地 SHA-256 |
| 恢复保护 | `--confirm-restore`、文件名/字节数/哈希校验、`pg_restore --list`、`--single-transaction` |
| 服务处理 | 恢复前停止 app/worker；成功或失败都尝试重新启动；最后检查 PostgreSQL ready |
| 输出 | `.dump` 自定义归档、`.dump.json` manifest、JSON 操作结果 |
| 敏感性 | dump 是客户私有数据；manifest 不含明文 Token，但两者都应保存在客户批准的加密存储中 |
| 不包含 | `.env`、Webhook secret 和明文 API Token 不在数据库备份中，必须由客户的秘密管理系统单独保存 |

## 备份

在仓库根目录运行。PowerShell、Command Prompt、bash 和 zsh 使用同一条 Python 命令：

```bash
python -m yanxu ops backup --compose-dir . --output backups/pilot-20260915.dump
```

命令拒绝覆盖已有的 dump 或 manifest。成功后得到：

```text
backups/pilot-20260915.dump
backups/pilot-20260915.dump.json
```

manifest 记录产品版本、数据库名、字节数和 SHA-256。备份文件仍包含令牌哈希、成本引用和交付元数据，不能提交到公开 GitHub。

如果服务使用 `docker compose -p NAME` 启动，先在当前终端设置同一项目名：

```powershell
$env:COMPOSE_PROJECT_NAME = "yanxu-pilot"
```

```bash
export COMPOSE_PROJECT_NAME=yanxu-pilot
```

## 恢复

恢复会替换当前私有数据库。先确认 dump 与同名 manifest 来自同一备份，再运行：

```bash
python -m yanxu ops restore backups/pilot-20260915.dump \
  --compose-dir . --confirm-restore
```

未提供确认参数、manifest 缺失、文件被改动、归档结构无效时，命令在停止服务前拒绝。数据库恢复使用单事务；恢复失败时不会提交部分对象，并会尝试重新启动 app/worker。

完成后核对：

```bash
docker compose ps
curl http://127.0.0.1:8080/healthz
```

PowerShell 使用：

```powershell
docker compose ps
Invoke-RestMethod http://127.0.0.1:8080/healthz
```

再登录 `/app`，检查工作空间、快照、成本和最近审计是否与备份时间一致。

## 升级步骤

1. 记录当前 tag、`docker compose ps` 和 `/healthz` 结果。
2. 使用当前版本执行 `ops backup`，把 dump、manifest 和当前 `.env` 的安全备份放入客户批准的位置。
3. 获取目标 tag，阅读 Release 说明，再切换到该 tag。
4. 执行 `docker compose build --pull app worker` 和 `docker compose up -d app worker`。PostgreSQL 卷保持不变。
5. 核对 `/healthz`、`/openapi.json` 的版本、登录、一个已知工作空间和 Worker delivery 状态。
6. 验证通过后保留升级前备份；不要立即删除旧 tag 或 dump。

升级失败时，先保存 `docker compose logs app worker db`。如果应用镜像无法启动且数据库未发生写入，可切回旧 tag 并重建 app/worker；如果目标版本已经改变数据库并需要回到备份时点，切回兼容 tag 后执行显式恢复。恢复会丢弃备份时间之后的数据，因此必须由客户负责人决定。

## 常见故障

| 现象 | 核对 | 处理 |
|---|---|---|
| `Docker CLI is not available` | `docker version`、`docker compose version` | 启动 Docker Desktop 或安装 Compose 插件后重试 |
| 找不到 `db` 服务 | 当前目录、`compose.yaml`、`COMPOSE_PROJECT_NAME` | 指向实际 compose 目录并使用启动时相同项目名 |
| 哈希不匹配 | dump 与 `.dump.json` 是否成对传输 | 停止恢复，重新复制或重新备份；不要绕过校验 |
| `pg_restore --list` 失败 | 文件大小、传输方式、磁盘空间 | 将文件视为损坏，不停止线上 app/worker |
| 恢复后无法登录 | `.env` 是否对应备份中的组织、Token 是否已撤销 | 使用客户保存的正确引导 Token；不要重置数据库来绕过鉴权 |
| delivery 长期 `PENDING` | `docker compose logs worker`、数据库健康 | 修复 Worker 后重启；delivery 幂等键避免同一事件重复处理 |

## 验收证据

本地合成 Docker 环境实测：备份生成 25,651 字节自定义归档及 SHA-256 `47b43a6e035d57db65d04eaac520758b3923b6f26f98c858b9ca56c28c6d8d99`。备份后成本从 1 条增加到 2 条；恢复完成并自动重启 app/worker 后回到 1 条，原 `USD 4.125` 仍为 `4125000` 微单位。

该结果证明本版本的备份/恢复路径在合成环境可运行，不代表客户灾备 SLA、跨云恢复或生产 RTO/RPO 已验收。正式试点应由客户确定备份频率、保留期、加密位置、RPO、RTO 和恢复演练责任人。
