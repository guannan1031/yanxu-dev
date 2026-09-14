# 私有试点初始化与预检

Yanxu Dev v0.19.0 把首次私有部署收敛成两个跨平台命令。目标用户是 5–50 人研发团队的试点交付负责人或客户运维人员；目标是避免手工复制示例密码、漏填环境变量或在沟通记录中回显组织 Token。

## 工作流

```mermaid
flowchart LR
    A[组织 slug / 名称 / 本机端口] --> B[pilot init]
    B --> C[随机密码 / Token / Webhook secret]
    C --> D[Git 忽略的 .env]
    D --> E[pilot doctor]
    E --> F{11 项预检}
    F -->|READY| G[docker compose up]
    F -->|NEEDS_WORK| H[按失败项修正]
    G --> I[/healthz 与浏览器登录]
```

| 项目 | 当前合同 |
|---|---|
| 输入 | 含 `compose.yaml` 的目录、合法组织 slug、1–120 字符组织名、1–65535 端口 |
| 生成 | 43 字符随机数据库密码、引导 Token 和 Webhook secret；本机 HTTP 的 secure cookie 开关；端口 |
| 保存 | 只写 Git 忽略的 `.env`；POSIX 系统权限设为 `0600`；已有文件时拒绝覆盖 |
| 输出 | 初始化结果只含路径、slug、端口和下一步，不含三个私密值 |
| 预检 | `.env` 格式、7 个必填键、示例占位符、私密值长度、slug、端口、Cookie 配置、Docker、Compose、`compose config --quiet` |
| 验收 | `pilot doctor` 输出脱敏 JSON；全部 11 项通过才返回 `READY` 和退出码 0，否则 `NEEDS_WORK` 和退出码 2 |
| 工具边界 | 初始化不联网、不启动容器；预检只调用 Docker/Compose 的版本和配置检查，不读取业务源码 |

## 初始化

Windows PowerShell、Command Prompt、macOS 和 Linux 使用同一条命令：

```bash
python -m yanxu pilot init \
  --compose-dir . \
  --org-slug platform-team \
  --org-name "Platform Team" \
  --port 8080
```

命令不会在终端打印数据库密码、Token 或 Webhook secret。管理员在客户批准的秘密管理流程中读取 `.env`，不得把它提交 Git、发送到公开聊天或放进演示视频。

如果 `.env` 已存在，命令停止并保留原文件。轮换已有部署的秘密不属于初始化流程，应先备份数据库并按变更窗口单独执行。

## 预检和启动

```bash
python -m yanxu pilot doctor --compose-dir . --output runs/pilot-doctor.json
docker compose up -d --build
```

`runs/pilot-doctor.json` 可交给客户验收，但它只有检查 id、状态和说明，不包含 `.env` 的值。启动后继续核对：

```bash
docker compose ps
curl http://127.0.0.1:8080/healthz
```

PowerShell 的健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/healthz
```

最后打开 `/login`，从客户自己的秘密存储读取 `YANXU_BOOTSTRAP_TOKEN` 登录。外部 HTTPS 入口必须在反向代理配置完成后把 `YANXU_SECURE_COOKIES` 改为 `true`，再重新运行 doctor。

## 验收证据

本地真实 Docker 冷启动使用 `pilot init` 生成三个 43 字符随机私密值，`.env` 权限为 `0600`；doctor 的 11 项检查全部 `PASS`，JSON 不含任一私密值。Compose 从空卷构建 v0.19.0，`/healthz` 返回数据库可用，OpenAPI 返回 `0.19.0`，组织 Token 建立 HttpOnly 会话并进入正确组织工作台，响应和页面未回显 Token。

这些是本地合成组织的安装验收。它没有证明客户网络、HTTPS 证书、GitHub App 权限或外部团队 UAT 已完成；正式试点仍需按客户环境逐项签字。

## 商业与面试价值

初始化和预检减少了每个试点重复手工配置的步骤，也生成可交付的脱敏检查证据。商业交付仍需要客户环境部署、GitHub App 配置、规则设计、真实任务运行和验收支持。

面试中可以表述：

> 我把私有部署的密码、组织 Token 和 Webhook secret 改为本地随机生成，初始化拒绝覆盖已有配置，预检只输出 11 项脱敏结果；同一流程在 Windows PowerShell 和 macOS/Linux 可用，并通过空卷 Docker 冷启动与真实浏览器会话验证。
