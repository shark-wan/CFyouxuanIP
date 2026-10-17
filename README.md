CFyouxuanIP 的筛选链路

GitHub Action 每 6 小时运行 `scripts/collect_cf_ips.py`，把来源中识别为 HK、JP、KR、SG、MY 的节点写入 `ip.aggregate.txt`。Android 设备读取这个原始汇聚文件，先对所有节点做 TCP connect 测试，删除不可达节点；再从可达的 SG/JP 节点中按 TCP 延迟选出前 10 个，使用 Xray 代理到非 Cloudflare 的 `proof.ovh.net` 文件测速。

设备生成的 `ip.txt` 格式保持为 `host:port#name`。前五行是全局下载速率排名，名称依次为 `AAA-...`、`BBB-...`、`CCC-...`、`DDD-...`、`EEE-...`；其余 TCP 可达节点按延迟排序，名称从 `1.JPxx`、`2.SGxx` 开始编号。首次汇聚、候选变化或测速算法更新时完整测速；之后每小时先对上一轮的五名做 TCP 存活检查，失效的节点直接删除，剩余节点全部重新测速后重排——排名不再固定前三位不变。

## Android 安装

设备需要 root `su`、Android 自带 `curl`/`nc`，以及 arm64。安装器会下载 Xray、写入 root 私有配置，并安装 `/data/adb/service.d/cfyouxuanip.sh`，设备重启后自动恢复。GitHub token 和订阅链接只写入设备的 `/data/adb/cfyouxuanip/config.env`，不会进入仓库：

```powershell
pwsh -File Android/install_android.ps1 `
  -GitHubToken $env:CF_GITHUB_TOKEN `
  -SubscriptionUrl 'https://example.invalid/sub?token=REPLACE_ME' `
  -BootstrapProxy 'socks5h://127.0.0.1:10808' `
  -DeviceId 'your-device-id'
```

`-BootstrapProxy` 只用于首次完整筛选前拉取 GitHub 汇聚文件；首轮完成后，设备用自己的 rank-1 Xray 在 `127.0.0.1:10809` SOCKS5 接管 GitHub raw/API 请求。也可以用 `-GitHubProxy` 指定一个长期外部代理，但这样会跳过设备 Xray 接管。默认测速使用非 Cloudflare 的 OVH 1 MiB 范围下载，取 2 次采样中位数，并按首字节到下载完成的时间换算字节/秒；没有收到数据的节点不会参与速率排名。可用 `CFY_TEST_BYTES` 和 `CFY_TEST_SAMPLES` 覆盖，GitHub runner 侧对应 `SG_TEST_BYTES` 和 `SG_TEST_SAMPLES`。

守护程序每小时提交 `ip.txt` 和 `device-status.json`。`.github/workflows/device-fallback.yml` 每小时第 15 分钟检查心跳，并分三级处理：

| 心跳年龄 | 行为 |
| --- | --- |
| 少于 90 分钟 | 正常退出，保留设备结果 |
| 90 分钟到 6 小时 | runner 自行 TCP + 测速，一级标签加 `旧-` 前缀（`旧-AAA-SG65`），提示这是旧设备数据的替代品 |
| 超过 6 小时 | 放弃 `AAA`–`EEE` 排序，直接把 `ip.aggregate.txt` 原文写入 `ip.txt` |

前两级都要求私有的 `CF_SUB_URL` Secret 且完成至少 5 个 SG/JP 下载测速，否则保留上一份有效排名，避免生成没有速率排名的文件。

日志和状态位于设备 `/data/adb/cfyouxuanip/worker.log`、`state.tsv`。手动运行一次可执行：

```sh
su -c /data/adb/cfyouxuanip/cfyouxuanipd.sh once
```
