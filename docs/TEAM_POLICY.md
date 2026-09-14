# Yanxu Team Policy

团队规则包把一次受控实现中允许修改的路径和批准测试命令固定为 JSON。它服务技术负责人审核，不替代 GitHub 分支保护、人工 Code Review 或 CI。

```bash
# --command 必须放在最后，后续参数按原样写入规则包
python -m yanxu policy --name "orders-service" \
  --allow-path src/main/java/com/example/orders/OrderService.java \
  --command mvn test

# 受控实现会拒绝超出规则包的路径或不同的测试命令
python -m yanxu implement runs/tasks/task.json --checkout . \
  --policy .yanxu/team-policy.json \
  --allow-path src/main/java/com/example/orders/OrderService.java \
  --output runs/implement --command mvn test
```

规则包包含名称、允许路径和测试命令。它不保存 Token、账号、仓库 URL 或业务源码。每次符合规则包的 `implement` 运行都会把规则包 SHA-256 写入 manifest，方便在交付看板或人工复核时确认执行约束。

## 商业价值

通用 AI Coding 工具解决“能否写代码”；团队规则包解决“哪些改动允许交给 AI、必须以什么测试证明”。这使 Yanxu Dev 可以作为私有化试点中的工程规则接入层：同一个客户规则可复用到同类任务，而不是每次由开发者口头说明边界。
