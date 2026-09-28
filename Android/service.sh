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

worker_alive() {
    pid=$1
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    [ -r "/proc/$pid/cmdline" ] || return 1
    cmdline=$(tr '\000' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    case "$cmdline" in
        *cfyouxuanipd.sh*) return 0 ;;
        *) return 1 ;;
    esac
}

if [ -s "$PIDFILE" ] && worker_alive "$(cat "$PIDFILE" 2>/dev/null)"; then
    exit 0
fi
start_worker

(
    while :; do
        pid=$(cat "$PIDFILE" 2>/dev/null || true)
        case "$pid" in
            ''|*[!0-9]*) start_worker ;;
            *) worker_alive "$pid" || start_worker ;;
        esac
        sleep 60
    done
) >/dev/null 2>&1 &
printf '%s' "$!" > "$WATCHDOG" 2>/dev/null || true
