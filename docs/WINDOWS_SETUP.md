# Windows 安装与迁移指南

这份指南用于把 Yanxu Dev v1.0+ 从 macOS 迁移到 Windows，并在新电脑上恢复开发、测试、受控代码生成、Draft PR 工作流和可选私有团队服务。

## 选择 Windows 原生模式

本项目建议使用 Windows 原生文件系统与 PowerShell：

- 项目放在 `C:\AI_Workspace\yanxu-dev`。
- Codex Agent 选择 `Windows native`，终端选择 `PowerShell`。
- 不需要为 Yanxu Dev 单独安装 WSL。其他项目依赖 Linux 工具时再启用 WSL2。

OpenAI 官方 Windows 文档确认桌面端支持原生 PowerShell、Git、worktree 和 Windows sandbox：<https://learn.chatgpt.com/docs/windows/windows-app>。

## 1. 安装四个工具

在 PowerShell 中安装 Git、Python 3.11、GitHub CLI 和 ChatGPT Windows 桌面端：

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.11 -e
winget install --id GitHub.cli -e
winget install --id 9PLM9XGG6VKS -s msstore
```

Yanxu 的 `implement` 命令需要 `codex` CLI。使用 OpenAI 官方 Windows 安装器：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
```

安装完成后关闭并重新打开 PowerShell，使 PATH 更新生效。

## 2. 在新电脑重新登录

```powershell
gh auth login
codex
```

首次运行 `codex` 时选择使用 ChatGPT 登录。不要从旧电脑复制 Token、Cookie、GitHub/Codex 登录缓存或 `.env`；在新电脑重新登录。

## 3. 克隆并固定换行符

```powershell
New-Item -ItemType Directory -Force C:\AI_Workspace | Out-Null
Set-Location C:\AI_Workspace
git -c core.autocrlf=false clone https://github.com/guannan1031/yanxu-dev.git
Set-Location .\yanxu-dev
git config --local core.autocrlf false
```

Yanxu 当前接受 UTF-8/LF 普通文本文件。关闭此仓库的自动 CRLF 转换，可以让本地行为与 Linux/Windows CI 保持一致。

## 4. 创建隔离环境并自检

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
.\scripts\windows-check.ps1 -RequireAuthTools
```

验收标准：脚本返回 `0 failure(s)`，完整测试通过，同时识别到 `gh` 与 `codex`。脚本只检查命令是否存在，不读取或输出登录凭据。

只做安装体验时，也可以从 [v0.20.0 Release](https://github.com/guannan1031/yanxu-dev/releases/tag/v0.20.0) 下载 `yanxu_dev-0.20.0-py3-none-any.whl`；其 SHA-256 为 `1caea10b6142d17395bab5801d20534ca6d4bc400239f239cc5ee6cc893165cb`。继续开发、运行仓库测试或启动 Docker Compose 时仍使用上面的 clone 流程。

## 5. 生成第一个本地任务合同

```powershell
python -m yanxu doctor --repo . --output runs\windows-doctor
python -m yanxu task "Improve one bounded Python function and preserve current behavior" --repo . --output runs\windows-task
```

这两条命令不修改 GitHub。输出 JSON、Markdown 和 HTML 证据后，再选择一个已存在的 UTF-8/LF 源码文件进行受控生成：

```powershell
python -m yanxu implement runs\windows-task\task.json --checkout . `
  --allow-path sample/pagination.py --output runs\windows-implement `
  --command python -m unittest discover -s tests -v
```

`implement` 只在不可变 HEAD 归档中应用模型补丁并测试，不改当前工作区，不推送 GitHub。人工审查通过后，才使用功能分支和 `draft-pr` 发布 Draft PR。

## 6. 每天开始开发时

```powershell
Set-Location C:\AI_Workspace\yanxu-dev
.\.venv\Scripts\Activate.ps1
git pull --ff-only
.\scripts\windows-check.ps1
```

## 7. 可选私有团队服务

先安装 Docker Desktop，保持 Linux containers 模式。核心 CLI 不需要 Docker；只有团队需要持久化 API 时才执行：

```powershell
python -m yanxu pilot init --org-slug example-team --org-name "Example Team"
python -m yanxu pilot doctor --output runs\pilot-doctor.json
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://127.0.0.1:8080/healthz
```

初始化和脱敏预检见[私有试点初始化](PILOT_ONBOARDING.md)，API、PowerShell 令牌用法和跨平台备份恢复见[私有团队服务说明](PRIVATE_SERVICE.md)。不要从旧电脑复制 GitHub/Codex 登录缓存；私有服务数据库迁移应使用该文档的 PostgreSQL 备份文件。

Windows 原生 PowerShell 可直接运行统一运维命令：

```powershell
python -m yanxu ops backup --compose-dir . --output backups\before-upgrade.dump
python -m yanxu ops restore backups\before-upgrade.dump --compose-dir . --confirm-restore
```

恢复会替换数据库，只能对已核对的 dump 与同名 `.json` manifest 执行。升级顺序和失败恢复见[备份、恢复与升级](UPGRADE_AND_RECOVERY.md)。

## 换机验收清单

- `git status --short --branch` 显示在 `main`，没有意外改动。
- `python --version` 为 3.11 或更高。
- `python -m unittest discover -s tests -v` 全部通过。
- `gh auth status` 显示 GitHub 已登录。
- `codex --version` 能运行。
- `python -m yanxu doctor ...` 生成 Windows 本地报告。
- GitHub Actions 的 Linux 和 Windows 检查通过。
- 如果启用私有服务，`pilot doctor` 全部通过，`/healthz` 返回数据库可用，`ops backup` 生成 dump 和 manifest，恢复后仍能读取备份时点的同一快照与成本。

达到以上条件后，可以继续录演示和开发。真实提效百分比仍要通过至少 5 个同范围、两侧质量均通过的配对任务测量，换机本身不会改变这个证据口径。
