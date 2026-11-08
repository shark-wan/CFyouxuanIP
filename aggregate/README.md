# edgetunnel 多来源优选地址聚合器

用 Python 并发请求多个 edgetunnel `BEST_SUB` 生成器，解析返回的 Base64 订阅，提取 `IPv4 / IPv6 / 域名 + 端口`，去重后按类型输出。无需安装第三方依赖。

## 运行

在 Windows PowerShell 中进入本目录后执行：

```powershell
py -3 .\aggregate.py
```

如果 Windows 没有安装 Python，可使用本机 WSL：

```powershell
wsl.exe -e python3 /mnt/c/Users/V9/Documents/Projects/code-youxuan-phone/aggregate/aggregate.py
```

## 配置

来源、超时、重试、并发数和输出目录在 `config.json` 中配置：

```json
{
  "sources": ["sub.cmliussss.net", "https://example.com/sub"],
  "timeout": 20,
  "retries": 2,
  "workers": 9,
  "output_dir": "output"
}
```

来源支持三种写法：

- 裸域名：`sub.cmliussss.net`
- edgetunnel sub URI：`sub://sub.cmliussss.net`
- 完整 URL：`https://sub.cmliussss.net/sub?host=example.com&uuid=00000000-0000-4000-8000-000000000000`

可在来源后加 `#标签`，例如 `sub.cmliussss.net#CM`。

## 常用参数

```powershell
# 临时指定来源
py -3 .\aggregate.py --source sub.cmliussss.net --source sub.keaeye.icu

# 指定输出目录、延长超时、增加重试
py -3 .\aggregate.py --output .\my-output --timeout 40 --retries 3

# 输出的地址备注不带来源标签
py -3 .\aggregate.py --no-source-remarks

# 跳过 TLS 证书校验（仅调试网络时使用）
py -3 .\aggregate.py --insecure
```

## 输出

运行后生成于 `aggregate/output/`：

| 文件 | 内容 |
|---|---|
| `addresses.txt` | 全部 `host:port#备注 [来源]`，可直接粘贴到 edgetunnel 的优选地址列表 |
| `ipv4.txt` | 仅 IPv4 |
| `ipv6.txt` | 仅 IPv6，格式为 `[IPv6]:端口` |
| `domains.txt` | 仅域名 |
| `results.json` | 明细，包含每个来源的请求状态、错误和地址去重信息 |

脚本会对 `host + port` 去重；同一地址来自多个来源时，会在 JSON 的 `sources` 数组和文本备注中合并显示。任一来源请求失败不会影响其他来源，但退出码为 `2`，便于定时任务感知部分失败。

## 注意

- 该脚本按 edgetunnel `BEST_SUB` 协议请求 `/sub?host=example.com&uuid=00000000-0000-4000-8000-000000000000`，并携带对应的 `User-Agent`。
- 当前已内置用户指定的 9 个来源。
- 输出中已包含一次真实运行结果；源站可用性随网络变化，重新运行即可刷新。
