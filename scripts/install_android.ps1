param(
    [Parameter(Mandatory = $true)] [string]$GitHubToken,
    [Parameter(Mandatory = $true)] [string]$SubscriptionUrl,
    [string]$Repo = "shark-wan/CFyouxuanIP",
    [string]$DeviceId = "df8a4a22",
    [string]$GitHubProxy = "",
    [string]$BootstrapProxy = "",
    [string]$Adb = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $Adb) {
    $Adb = Join-Path $repoRoot "..\Android-Project\.tools\android-sdk\platform-tools\adb.exe"
}
if (-not (Test-Path $Adb)) { throw "adb.exe not found: $Adb" }

$work = Join-Path $env:TEMP "cfyouxuanip-install"
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $work | Out-Null

$zip = Join-Path $work "xray.zip"
$extract = Join-Path $work "xray"
Invoke-WebRequest -Uri "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-android-arm64-v8a.zip" -OutFile $zip
Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
$xray = Get-ChildItem -Path $extract -Recurse -File | Where-Object { $_.Name -eq "xray" } | Select-Object -First 1
if (-not $xray) { throw "The Xray release did not contain an arm64 xray binary" }

$cfg = Join-Path $work "config.env"
@"
CFY_REPO='$Repo'
CFY_BRANCH='main'
CFY_GITHUB_TOKEN='$GitHubToken'
CFY_SUB_URL='$SubscriptionUrl'
CFY_DEVICE_ID='$DeviceId'
CFY_GITHUB_PROXY='$GitHubProxy'
CFY_BOOTSTRAP_PROXY='$BootstrapProxy'
CFY_AUTO_PROXY='1'
CFY_PROXY_PORT='10809'
CFY_POLL_SECONDS='3600'
CFY_TEST_URL='https://proof.ovh.net/files/10Mb.dat'
CFY_TEST_BYTES='131072'
"@ | Set-Content -LiteralPath $cfg -Encoding ascii

& $Adb wait-for-device
& $Adb push (Join-Path $repoRoot "scripts\cfyouxuanipd.sh") /data/local/tmp/cfyouxuanipd.sh | Out-Null
& $Adb push $xray.FullName /data/local/tmp/xray | Out-Null
& $Adb push $cfg /data/local/tmp/cfyouxuanip.env | Out-Null
& $Adb shell su -c "mkdir -p /data/adb/cfyouxuanip /data/adb/service.d"
& $Adb shell su -c "cp /data/local/tmp/cfyouxuanipd.sh /data/adb/cfyouxuanip/cfyouxuanipd.sh"
& $Adb shell su -c "cp /data/local/tmp/xray /data/adb/cfyouxuanip/xray"
& $Adb shell su -c "cp /data/local/tmp/cfyouxuanip.env /data/adb/cfyouxuanip/config.env"
& $Adb shell su -c "chmod 700 /data/adb/cfyouxuanip/cfyouxuanipd.sh"
& $Adb shell su -c "chmod 700 /data/adb/cfyouxuanip/xray"
& $Adb shell su -c "chmod 600 /data/adb/cfyouxuanip/config.env"

$service = @'
#!/system/bin/sh
BASE=/data/adb/cfyouxuanip
if [ -x "$BASE/cfyouxuanipd.sh" ]; then
    pkill -f "$BASE/cfyouxuanipd.sh" >/dev/null 2>&1 || true
    nohup "$BASE/cfyouxuanipd.sh" daemon >> "$BASE/worker.log" 2>&1 &
fi
'@
$servicePath = Join-Path $work "service.sh"
$service | Set-Content -LiteralPath $servicePath -Encoding ascii
& $Adb push $servicePath /data/local/tmp/cfyouxuanip-service.sh | Out-Null
& $Adb shell su -c "cp /data/local/tmp/cfyouxuanip-service.sh /data/adb/service.d/cfyouxuanip.sh"
& $Adb shell su -c "chmod 700 /data/adb/service.d/cfyouxuanip.sh"
& $Adb shell su -c "/data/adb/cfyouxuanip/cfyouxuanipd.sh once" | Out-Null
Write-Host "CFyouxuanIP Android worker installed. State: /data/adb/cfyouxuanip"
