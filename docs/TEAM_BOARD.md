# Yanxu Delivery Board

`board` 将**用户明确指定**的本地 Yanxu 运行产物汇总成静态 HTML 和 JSON。它面向单仓库试点负责人，用来查看受控实现、交付工作流、隔离测试和测量结论，而不建立托管服务。

```bash
# 只读取 runs/ 下已经存在的 JSON 产物
python -m yanxu board --runs runs --output runs/board.html

# 可选：加入由 record 写入的真实观察数据
python -m yanxu board --runs runs --measurements runs/observed.json --output runs/board.html
```

## 输入、输出与边界

| 项目 | 说明 |
| --- | --- |
| 输入 | `yanxu.delivery_workflow` 与 `yanxu.implementation_run` JSON；可选的 `observed` 测量数据 |
| 处理 | 只读扫描最多 200 个、单个不超过 1 MB 的本地 JSON；不解析业务源码；无效或不支持的 JSON 忽略 |
| 输出 | `board.html` 与同目录 `board.json` |
| 验收 | 页面显示记录运行数、隔离测试、人工复核状态和测量口径；所有不可信文本 HTML 转义 |

看板不创建 PR、不推送、不 merge、不部署，也不把任何运行产物上传给 Yanxu。若测量有效样本少于 5 个或范围/质量不匹配，页面保持“不可计算”，不显示效率百分比。

## 试点与商业价值

首个付费试点可以交付为“本地开源核心 + 团队规则配置 + 工作流接入 + 交付证据看板 + 固定期限维护”。客户保留代码、凭据和执行环境；Yanxu 提供跨任务的约束、证据、测量和接入服务。托管账号、多人权限、客户 Runner 和私有部署编排仍是后续产品，不在此命令中宣称已经实现。
