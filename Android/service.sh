#!/system/bin/sh
BASE=/data/adb/cfyouxuanip
PIDFILE=$BASE/worker.pid
WATCHDOG=$BASE/watchdog.pid

[ -x "$BASE/cfyouxuanipd.sh" ] || exit 0
mkdir -p "$BASE"

# Do not use pkill -f here: another shell or a manual run can have a similar
# command line.  A small supervisor also recovers from an OOM kill or a
# transient process exit without waiting for the next device reboot.
start_worker() {
    nohup "$BASE/cfyouxuanipd.sh" daemon >> "$BASE/worker.log" 2>&1 &
    printf '%s' "$!" > "$PIDFILE"
}

if [ -s "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
    exit 0
fi
start_worker

(
    while :; do
        pid=$(cat "$PIDFILE" 2>/dev/null || true)
        case "$pid" in
            ''|*[!0-9]*) start_worker ;;
            *) kill -0 "$pid" 2>/dev/null || start_worker ;;
        esac
        sleep 60
    done
) >/dev/null 2>&1 &
printf '%s' "$!" > "$WATCHDOG" 2>/dev/null || true
