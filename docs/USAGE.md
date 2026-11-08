# 使用说明

## 1. 本地测试

在仓库根目录执行：

```bash
python3 cfyouxuan.py run
```

可选参数：

```bash
python3 cfyouxuan.py run \
  --ip-file engine/cfst/ip.txt \
  --mode httping \
  --routines 100 \
  --ping-count 4 \
  --download-count 10 \
  --download-time 5 \
  --max-latency 300 \
  --max-loss-rate 0.2 \
  --top 20 \
  --repo-root . \
  --output-dir output
```

说明：

- `--mode httping`：HTTP 延迟测试，能拿到地区码。
- `--mode tcping`：TCP 延迟测试，速度更快，但没有地区码。
- `--routines`：并发数，轻量 ARM 服务器建议 64~128。
- `--ping-count`：单个 IP 的测试次数，建议 4。
- `--download-count`：参与下载测速的前 N 个 IP，建议 10。
- `--download-time`：单个 IP 的下载测速秒数，建议 5~10。
- `--max-latency`：平均延迟上限，建议 300ms。
- `--max-loss-rate`：丢包率上限，建议 0.2。
- `--top`：最终 `ip.txt` 里本地优选 IP 的数量，建议 20。
- `--repo-root`：结果要写回的 Git 仓库目录。
- `--output-dir`：本地输出目录。

## 2. 聚合来源

聚合脚本位于 `aggregate/aggregate.py`，默认配置在 `aggregate/config.json`。

可单独运行：

```bash
python3 aggregate/aggregate.py
```

输出在 `aggregate/output/`：

- `addresses.txt`
- `domains.txt`
- `ipv4.txt`
- `ipv6.txt`
- `results.json`

主流程会自动调用聚合脚本，并把结果放在本地优选 IP 之后。

## 3. 输出文件

主流程会生成：

- `ip.txt`：最终输出，前 N 个为本地优选，后面为聚合来源
- `ip.local.txt`：仅本地优选结果
- `ip.aggregate.txt`：仅聚合来源结果
- `device-status.json`：更新时间、数量、模式、引擎版本

格式统一为：

```text
host:port#remark
```

本地结果备注格式：

```text
104.16.215.91:443#LAX
```

聚合结果备注格式：

```text
91.107.166.26:8888#DE
```

## 4. 推送到 GitHub

如果要自动推送到 GitHub：

```bash
python3 cfyouxuan.py run --push --repo-root /path/to/CFyouxuanIP
```

目标仓库：

- <https://github.com/shark-wan/CFyouxuanIP>
- 输出文件：`ip.txt`

## 5. 定时运行

Linux ARM64 服务器上可安装 systemd 定时器：

```bash
sudo bash scripts/install.sh /opt/CFyouxuanIP
```

定时时间：

- 06:30
- 11:30
- 17:30
- 23:30

查看状态：

```bash
systemctl list-timers cfyouxuan.timer
journalctl -u cfyouxuan.service -f
```
