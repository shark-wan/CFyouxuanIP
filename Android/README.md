# Android 部署记录

本目录记录 CFyouxuanIP 在一台已取得 root 权限的 Android 16 arm64 设备上的部署方式。公开文件不包含 GitHub token、订阅 token、设备私有状态、节点测速结果或 Xray 二进制文件。

## 5W1H

### What：部署了什么

- `cfyouxuanipd.sh`：Android 常驻 worker。
- `install_android.ps1`：从 Windows 主机通过 ADB 安装 worker 和 Xray。
- `service.sh`：KernelSU/`/data/adb/service.d` 启动脚本，负责重启后拉起 worker，并每 60 秒检查 `worker.pid`，在 worker 被系统回收或异常退出时自动重启。
- Xray arm64：从官方 release 下载到设备的 `/data/adb/cfyouxuanip/xray`，只在设备本地使用，不提交二进制。
- 私有配置：设备上的 `/data/adb/cfyouxuanip/config.env`，权限为 `0600`。

worker 每小时从 GitHub 获取 `ip.aggregate.txt`，对全部节点执行 TCP connect 测试；从可达的 SG/JP 节点中按延迟取前 10 个，用 Xray 连接到订阅节点，再通过非 Cloudflare 的 OVH 文件测下载速度。结果写成 `ip.txt`：第 1 至 5 行使用 `AAA`、`BBB`、`CCC`、`DDD`、`EEE`，第 6 行起按 TCP 延迟排列其余可达节点。

首次汇聚或候选集合变化时执行完整测速。普通小时只重新测试上一轮第 4、5 名，并与上一轮第 1、2、3 名比较后重排。设备还提交 `device-status.json` 作为心跳。

### Why：为什么这样部署

- Android 端直接完成 TCP 过滤和测速，减少受 GitHub runner 网络位置影响的结果差异。
- Xray 负责 VLESS/XHTTP 节点连接和 GitHub 上传，避免依赖 Windows 主机持续在线。
- `service.d` 适配 KernelSU/类似 root 启动目录，设备重启后自动恢复。
- GitHub Action 只负责汇聚和心跳过期时的兜底，避免设备正常运行时覆盖设备排名。

### Who：谁负责什么

- GitHub Action：运行 `scripts/collect_cf_ips.py`，更新 `ip.aggregate.txt`；心跳超过 90 分钟时运行 `device-fallback.yml`。
- Android worker：读取汇聚、探测 TCP、测速、更新 `ip.txt`，并提交 `device-status.json`。
- Xray：在 Android 上提供本地 SOCKS5，供 GitHub raw/API 访问和测速请求使用。
- 管理者：只在设备私有配置中提供 token 和订阅链接，并负责撤销泄露或过期 token。

### When：什么时候运行

- GitHub 汇聚：默认每 6 小时一次。
- Android worker：默认每 1 小时一次；设备启动脚本会先启动守护进程。
- 完整测速：首次运行或 `ip.aggregate.txt` 内容变化时。
- 增量测速：其他小时，只测上一轮第 4、5 名。
- GitHub 兜底：每小时检查一次；`device-status.json` 超过 90 分钟未更新才执行。

### Where：文件和服务在哪里

设备私有目录：

```text
/data/adb/cfyouxuanip/
  config.env          # 0600，含 token 和订阅链接
  cfyouxuanipd.sh     # worker
  xray                # arm64 Xray 二进制
  state.tsv           # 排名和测速状态
  subscription.txt    # 当前订阅解码结果
  ip.aggregate.txt    # GitHub 汇聚副本
  ip.txt              # 设备生成结果
  device-status.json  # 心跳状态
  worker.log          # 最近运行日志

/data/adb/service.d/cfyouxuanip.sh
```

仓库公开目录：

```text
Android/
  README.md
  config.env.example
  cfyouxuanipd.sh
  install_android.ps1
  service.sh
```

### How：如何部署

1. 准备 Android 16 arm64 设备，打开 USB 调试，并确认 `adb shell su -c id` 返回 `uid=0(root)`。
2. 在仓库根目录执行安装器。token 和订阅链接只作为命令参数写入设备私有配置，不要写入仓库：

   ```powershell
   pwsh -File Android/install_android.ps1 `
     -GitHubToken $env:CF_GITHUB_TOKEN `
     -SubscriptionUrl 'https://example.invalid/sub?token=REPLACE_ME' `
     -DeviceId 'android-16-01'
   ```

3. 如果首次访问 GitHub 需要临时引导代理，额外传入 `-BootstrapProxy 'socks5h://127.0.0.1:10808'`。首轮完成后，worker 会用自己的 rank-1 Xray 在设备本地 `127.0.0.1:10809` 访问 GitHub。
4. 检查服务和脚本：

   ```powershell
   adb shell su -c 'sh -n /data/adb/cfyouxuanip/cfyouxuanipd.sh'
   adb shell su -c 'sh -n /data/adb/service.d/cfyouxuanip.sh'
   adb shell su -c 'ls -l /data/adb/cfyouxuanip /data/adb/service.d'
   ```

5. 手动运行一次：

   ```powershell
   adb shell su -c '/data/adb/cfyouxuanip/cfyouxuanipd.sh once'
   ```

6. 确认 GitHub 上的 `ip.txt` 和 `device-status.json` 更新时间正常。若希望设备离线时由 GitHub runner 继续测速，可把订阅放入仓库私有 Secret `CF_SUB_URL`；不设置时兜底会保留上一份有效排名，不会把未测速的 TCP 列表写进 `ip.txt`。

## 敏感信息规则

- 不提交真实 GitHub token、订阅 URL、Xray JSON、`subscription.raw`、`subscription.txt`、`state.tsv`、日志或设备 IP 排名结果。
- 使用 `config.env.example` 作为字段说明；真实配置只放在 Android 的 `/data/adb/cfyouxuanip/config.env`。
- token 曾经在聊天或命令历史中出现时，应立即在 GitHub 设置中撤销并重新生成。
