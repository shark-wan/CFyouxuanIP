# CFyouxuanIP

本仓库包含本地 Cloudflare 优选 IP 测试、聚合与发布流程。

- 本地测速引擎：`engine/cfst/`，来自 [XIU2/CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest)。
- 多来源聚合：`aggregate/aggregate.py`。
- 主流程：`cfyouxuan.py`。
- 定时任务：`systemd/cfyouxuan.timer`。

本项目包含 GPL-3.0 代码，因此整体按 GPL-3.0 发布。
