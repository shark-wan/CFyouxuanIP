# 部署说明

## 目标环境

- Linux ARM64 / aarch64
- Python 3.10+
- systemd
- 不依赖 Docker

## 安装

在仓库根目录执行：

```bash
sudo bash scripts/install.sh /opt/CFyouxuanIP
```

安装脚本会：

1. 复制代码到 `/opt/CFyouxuanIP`
2. 下载 CloudflareSpeedTest ARM64 二进制
3. 安装 `cfyouxuan.service`
4. 安装 `cfyouxuan.timer`
5. 启用每天 4 次定时任务

## 手动运行一次

```bash
cd /opt/CFyouxuanIP
python3 cfyouxuan.py run --push
```

## 查看结果

```bash
cat /opt/CFyouxuanIP/output/ip.txt
```

## 查看定时任务

```bash
systemctl list-timers cfyouxuan.timer
```

## 查看日志

```bash
journalctl -u cfyouxuan.service -f
```
