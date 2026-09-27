#!/system/bin/sh
BASE=/data/adb/cfyouxuanip
if [ -x "$BASE/cfyouxuanipd.sh" ]; then
    pkill -f "$BASE/cfyouxuanipd.sh" >/dev/null 2>&1 || true
    nohup "$BASE/cfyouxuanipd.sh" daemon >> "$BASE/worker.log" 2>&1 &
fi
