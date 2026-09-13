# v0.7：AI Coding 项目体检

`doctor` 在开发开始前检查仓库是否具备受约束 AI Coding 的基本条件。目标用户是准备让 AI Agent 参与日常研发的开发者和维护者。

```bash
python -m yanxu doctor --repo /path/to/repo --output runs/doctor
```

## 检查项

- `AGENTS.md`：项目范围、命令、边界和验收规则；
- `README.md`：安装、启动和验证入口；
- 标准构建元数据；
- 可检测的测试入口；
- GitHub CI 工作流；
- `.env` 和私钥排除或安全说明。

输出 `doctor.json`、`doctor.md` 和独立 `doctor.html`。关键项缺失时状态为 `NEEDS_WORK`，报告给出最小整改动作；条件满足时为 `READY`。分数用于定位缺口，不代表代码质量、安全认证或生产就绪。

## 工具边界与验收

体检只读取项目规则、README、构建配置、CI、`.gitignore` 和安全说明等工程元数据，业务源码扫描数固定为 0；不运行代码、不调用模型、不访问 GitHub、不修改远端。验收要求是缺失项能被稳定识别，报告路径可打开，HTML 无浏览器报错。
