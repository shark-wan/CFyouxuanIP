CFyouxuanIP 的筛选链路

GitHub Action 每 6 小时运行 `scripts/collect_cf_ips.py`，把来源中识别为 HK、JP、KR、SG、MY 的节点写入 `ip.aggregate.txt`。Android 设备读取这个原始汇聚文件，先对所有节点做 TCP connect 测试，删除不可达节点；再从可达的 SG/JP 节点中按 TCP 延迟选出前 10 个，使用 Xray 代理到非 Cloudflare 的 `proof.ovh.net` 文件测速。

设备生成的 `ip.txt` 格式保持为 `host:port#name`。前五行是全局下载速率排名，名称依次为 `AAA-...`、`BBB-...`、`CCC-...`、`DDD-...`、`EEE-...`；第六行起是其余 TCP 可达节点，按延迟排序。首次汇聚或候选变化时完整测速；之后每小时只重测上一轮的第 4、5 名，并与上一轮第 1、2、3 名的速度比较后重排。

## Android 安装

设备需要 root `su`、Android 自带 `curl`/`nc`，以及 arm64。安装器会下载 Xray、写入 root 私有配置，并安装 `/data/adb/service.d/cfyouxuanip.sh`，设备重启后自动恢复。GitHub token 和订阅链接只写入设备的 `/data/adb/cfyouxuanip/config.env`，不会进入仓库：

```powershell
pwsh -File scripts/install_android.ps1 `
  -GitHubToken $env:CF_GITHUB_TOKEN `
  -SubscriptionUrl 'https://example.invalid/sub?token=REPLACE_ME' `
  -DeviceId 'your-device-id'
```

守护程序每小时提交 `ip.txt` 和 `device-status.json`。`device-status.json` 超过 90 分钟没有更新时，`.github/workflows/device-fallback.yml` 会在 GitHub runner 上执行 TCP 筛选和测速；设置仓库 Secret `CF_SUB_URL` 后兜底也能使用 Xray 订阅测速，没有该 Secret 时仍会发布 TCP 可达列表。

日志和状态位于设备 `/data/adb/cfyouxuanip/worker.log`、`state.tsv`。手动运行一次可执行：

```sh
su -c /data/adb/cfyouxuanip/cfyouxuanipd.sh once
```
