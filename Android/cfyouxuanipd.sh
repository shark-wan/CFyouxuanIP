#!/system/bin/sh
# Android worker for CFyouxuanIP.  It intentionally uses only Android's
# built-in shell tools plus an Xray binary installed by install_android.ps1.

set -u

BASE=${CFY_BASE:-/data/adb/cfyouxuanip}
CONFIG=${CFY_CONFIG:-$BASE/config.env}
[ -r "$CONFIG" ] || exit 1
. "$CONFIG"

REPO=${CFY_REPO:-shark-wan/CFyouxuanIP}
BRANCH=${CFY_BRANCH:-main}
TOKEN=${CFY_GITHUB_TOKEN:-}
SUB_URL=${CFY_SUB_URL:-}
XRAY=${CFY_XRAY:-$BASE/xray}
TEST_URL=${CFY_TEST_URL:-https://proof.ovh.net/files/10Mb.dat}
TEST_BYTES=${CFY_TEST_BYTES:-1048576}
TEST_SAMPLES=${CFY_TEST_SAMPLES:-2}
POLL_SECONDS=${CFY_POLL_SECONDS:-3600}
TCP_TIMEOUT=${CFY_TCP_TIMEOUT:-4}
GITHUB_PROXY=${CFY_GITHUB_PROXY:-}
BOOTSTRAP_PROXY=${CFY_BOOTSTRAP_PROXY:-}
AUTO_PROXY=${CFY_AUTO_PROXY:-1}
AUTO_PROXY_BOOTSTRAP=${CFY_AUTO_PROXY_BOOTSTRAP:-0}
STATE=$BASE/state.tsv
MEASUREMENT_VERSION=body-rate-v2
MEASUREMENT_FILE=$BASE/measurement.version
RUN_LOCK=$BASE/run.lock
AGGREGATE=$BASE/ip.aggregate.txt
REACHABLE=$BASE/reachable.tsv
SUBSCRIPTION=$BASE/subscription.txt
LOG=$BASE/worker.log
API=https://api.github.com/repos/$REPO/contents
RAW=https://raw.githubusercontent.com/$REPO/$BRANCH/ip.aggregate.txt
DEVICE_ID=${CFY_DEVICE_ID:-unknown}
TAB=$(printf '\t')

mkdir -p "$BASE"
umask 077

log() {
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG"
    tail -c 262144 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
}

die() { log "ERROR: $*"; exit 1; }

acquire_run_lock() {
    if [ -d "$RUN_LOCK" ]; then
        old_pid=$(cat "$RUN_LOCK/pid" 2>/dev/null || true)
        case "$old_pid" in
            ''|*[!0-9]*) rm -rf "$RUN_LOCK" ;;
            *)
                if ! kill -0 "$old_pid" 2>/dev/null; then
                    rm -rf "$RUN_LOCK"
                fi
                ;;
        esac
    fi
    if ! mkdir "$RUN_LOCK" 2>/dev/null; then
        log 'run skipped: another worker invocation holds the lock'
        return 1
    fi
    printf '%s\n' "$$" > "$RUN_LOCK/pid"
    return 0
}

release_run_lock() {
    rm -rf "$RUN_LOCK"
}

run_once_locked() {
    acquire_run_lock || return 0
    run_once
    rc=$?
    release_run_lock
    return "$rc"
}

url_decode() {
    # Subscription links use these escapes for JSON and paths.  Values are
    # otherwise kept untouched so names and hosts remain readable.
    printf '%s' "$1" | sed \
        -e 's/%7B/{/g' -e 's/%7b/{/g' -e 's/%7D/}/g' -e 's/%7d/}/g' \
        -e 's/%22/"/g' -e 's/%3A/:/g' -e 's/%3a/:/g' -e 's/%2C/,/g' -e 's/%2c/,/g' \
        -e 's#%2F#/#g' -e 's#%2f#/#g' -e 's#%3F#?#g' -e 's#%3f#?#g' \
        -e 's#%3D#=#g' -e 's#%3d#=#g' -e 's#%26#\\&#g' -e 's#%25#%#g'
}

param() {
    printf '%s' "$1" | tr '&' '\n' | sed -n "s/^$2=//p" | head -n 1
}

json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

endpoint_key() { printf '%s:%s\n' "$1" "$2"; }

github_curl() {
    if [ -n "$GITHUB_PROXY" ]; then
        curl --proxy "$GITHUB_PROXY" "$@"
    else
        curl "$@"
    fi
}

tcp_ms() {
    host=$1; port=$2
    case "$host" in
        \[*\]) host=${host#\[}; host=${host%\]}; nc_args="-6" ;;
        *:*) nc_args="-6" ;;
        *) nc_args="-4" ;;
    esac
    started=$(date +%s%3N)
    if timeout "$TCP_TIMEOUT" nc $nc_args -n -w "$TCP_TIMEOUT" "$host" "$port" </dev/null >/dev/null 2>&1; then
        finished=$(date +%s%3N)
        value=$((finished - started))
        [ "$value" -gt 0 ] || value=1
        printf '%s' "$value"
    else
        return 1
    fi
}

fetch_aggregate() {
    tmp=$AGGREGATE.tmp
    if ! github_curl -fsSL --connect-timeout 20 --max-time 90 "$RAW?ts=$(date +%s)" -o "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    [ -s "$tmp" ] || { rm -f "$tmp"; return 1; }
    mv "$tmp" "$AGGREGATE"
}

probe_aggregate() {
    probe_dir=$BASE/probes
    rm -rf "$probe_dir"
    mkdir -p "$probe_dir"
    pids=''; index=0; running=0
    while IFS= read -r line || [ -n "$line" ]; do
        line=$(printf '%s' "$line" | tr -d '\r')
        [ -n "$line" ] || continue
        case "$line" in *#*) ;; *) continue ;; esac
        address=${line%%#*}; name=${line#*#}
        case "$address" in *:*) ;; *) continue ;; esac
        host=${address%:*}; port=${address##*:}
        case "$port" in ''|*[!0-9]*) continue ;; esac
        index=$((index + 1)); task=$probe_dir/$index
        (
            if latency=$(tcp_ms "$host" "$port"); then
                printf '%s\t%s\t%s\t%s\n' "$host" "$port" "$name" "$latency" > "$task"
            fi
        ) &
        pids="$pids $!"; running=$((running + 1))
        if [ "$running" -ge 32 ]; then
            for pid in $pids; do wait "$pid" 2>/dev/null || true; done
            pids=''; running=0
        fi
    done < "$AGGREGATE"
    for pid in $pids; do wait "$pid" 2>/dev/null || true; done
    : > "$REACHABLE.tmp"
    cat "$probe_dir"/* > "$REACHABLE.tmp" 2>/dev/null || true
    sort -t '	' -k4,4n "$REACHABLE.tmp" > "$REACHABLE"
    rm -rf "$probe_dir" "$REACHABLE.tmp"
    wc -l < "$REACHABLE" | tr -d ' '
}

fetch_subscription() {
    [ -n "$SUB_URL" ] || return 1
    raw=$BASE/subscription.raw
    if [ -n "$GITHUB_PROXY" ]; then
        curl --proxy "$GITHUB_PROXY" -fsSL --connect-timeout 20 --max-time 90 "$SUB_URL" -o "$raw" || return 1
    else
        curl -fsSL --connect-timeout 20 --max-time 90 "$SUB_URL" -o "$raw" || return 1
    fi
    first=$(head -n 1 "$raw")
    case "$first" in
        vless://*|vmess://*) cp "$raw" "$SUBSCRIPTION" ;;
        *) base64 -d "$raw" > "$SUBSCRIPTION" 2>/dev/null || cp "$raw" "$SUBSCRIPTION" ;;
    esac
    [ -s "$SUBSCRIPTION" ]
}

find_uri() {
    host=$1; port=$2
    case "$host" in
        \[*\]) host=${host#\[}; host=${host%\]} ;;
    esac
    # Match the normalized endpoint directly to preserve its UUID and
    # transport parameters.  Do not use grep|head with ||: head succeeds even
    # when grep found no line, which would skip the IPv6 fallback.
    uri=$(grep -F "@$host:$port?" "$SUBSCRIPTION" | head -n 1 || true)
    if [ -z "$uri" ]; then
        uri=$(grep -F "@[$host]:$port?" "$SUBSCRIPTION" | head -n 1 || true)
    fi
    [ -n "$uri" ] && printf '%s\n' "$uri"
}

write_xray_config() {
    uri=$1; socks=$2; output=$3
    auth=${uri#vless://}; uuid=${auth%@*}; rest=${auth#*@}
    endpoint=${rest%%\?*}; queryfrag=${rest#*\?}; query=${queryfrag%%#*}
    host=${endpoint%:*}; node_port=${endpoint##*:}
    security=$(param "$query" security); [ -n "$security" ] || security=none
    network=$(param "$query" type); [ -n "$network" ] || network=tcp
    sni=$(url_decode "$(param "$query" sni)")
    fp=$(url_decode "$(param "$query" fp)")
    alpn=$(url_decode "$(param "$query" alpn)")
    path=$(url_decode "$(param "$query" path)"); [ -n "$path" ] || path=/
    transport_host=$(url_decode "$(param "$query" host)")
    transport_mode=$(url_decode "$(param "$query" mode)")
    extra=$(url_decode "$(param "$query" extra)")
    log "proxy candidate endpoint=$endpoint security=$security network=$network host=$transport_host path=$path"
    host_json=$(json_escape "$transport_host"); path_json=$(json_escape "$path")
    sni_json=$(json_escape "$sni"); fp_json=$(json_escape "$fp")
    stream="\"network\":\"$(json_escape "$network")\",\"security\":\"$(json_escape "$security")\""
    if [ "$security" = tls ] || [ "$security" = reality ]; then
        tls="\"tlsSettings\":{"
        [ -n "$sni" ] && tls="$tls\"serverName\":\"$sni_json\","
        [ -n "$fp" ] && tls="$tls\"fingerprint\":\"$fp_json\","
        [ -n "$alpn" ] && tls="$tls\"alpn\":[\"$(printf '%s' "$alpn" | sed 's/,/\",\"/g')\"],"
        tls=${tls%,}; tls="$tls}"
        stream="$stream,$tls"
    fi
    if [ "$network" = xhttp ]; then
        xhttp="\"xhttpSettings\":{\"path\":\"$path_json\""
        [ -n "$transport_host" ] && xhttp="$xhttp,\"host\":\"$host_json\""
        [ -n "$transport_mode" ] && xhttp="$xhttp,\"mode\":\"$(json_escape "$transport_mode")\""
        case "$extra" in \{*\}) xhttp="$xhttp,\"extra\":$extra" ;; esac
        xhttp="$xhttp}"
        stream="$stream,$xhttp"
    elif [ "$network" = ws ]; then
        ws="\"wsSettings\":{\"path\":\"$path_json\""
        [ -n "$transport_host" ] && ws="$ws,\"headers\":{\"Host\":\"$host_json\"}"
        ws="$ws}"
        stream="$stream,$ws"
    fi
    cat > "$output" <<EOF
{"log":{"loglevel":"error"},"inbounds":[{"listen":"127.0.0.1","port":$socks,"protocol":"socks","settings":{"udp":false}}],"outbounds":[{"protocol":"vless","settings":{"vnext":[{"address":"$(json_escape "$host")","port":$node_port,"users":[{"id":"$(json_escape "$uuid")","encryption":"none"}]}]},"streamSettings":{$stream}},{"protocol":"freedom"}]}
EOF
}

speed_probe() {
    uri=$1
    socks_port=$((18080 + ($$ % 1000)))
    cfg=$BASE/xray-speed.json
    write_xray_config "$uri" "$socks_port" "$cfg" || return 1
    "$XRAY" run -c "$cfg" >/dev/null 2>&1 &
    xpid=$!
    ready=0
    i=0
    while [ "$i" -lt 50 ]; do
        if nc -n -w 1 127.0.0.1 "$socks_port" </dev/null >/dev/null 2>&1; then ready=1; break; fi
        sleep 0.1; i=$((i + 1))
    done

    speeds=''
    if [ "$ready" -eq 1 ]; then
        sample=0
        while [ "$sample" -lt "$TEST_SAMPLES" ]; do
            result=$(curl -fsSL --proxy "socks5h://127.0.0.1:$socks_port" --connect-timeout 8 --max-time 35 --range "0-$((TEST_BYTES - 1))" -o /dev/null -w '%{size_download}	%{time_starttransfer}	%{time_total}' "$TEST_URL" 2>/dev/null || true)
            log "speed curl endpoint=$socks_port sample=$sample result=$result"

            size=${result%%"$TAB"*}
            remainder=${result#*"$TAB"}
            first_byte=${remainder%%"$TAB"*}
            seconds=${remainder#*"$TAB"}

            case "$size" in ''|0|*[!0-9]*) log "speed sample $sample: invalid size"; sample=$((sample + 1)); continue ;; esac
            case "$first_byte" in ''|*[!0-9.]*) log "speed sample $sample: invalid first_byte"; sample=$((sample + 1)); continue ;; esac
            case "$seconds" in ''|0|*[!0-9.]*) log "speed sample $sample: invalid seconds"; sample=$((sample + 1)); continue ;; esac

            bps=$(awk -v s="$size" -v f="$first_byte" -v t="$seconds" 'BEGIN { d=t-f; if (d > 0) printf "%.0f", s/d; }')
            case "$bps" in ''|0|*[!0-9]*) log "speed sample $sample: invalid bps"; sample=$((sample + 1)); continue ;; esac

            speeds="$speeds $bps"
            sample=$((sample + 1))
        done
    fi

    kill "$xpid" >/dev/null 2>&1 || true
    wait "$xpid" 2>/dev/null || true
    rm -f "$cfg"

    # 计算中位数
    if [ -z "$speeds" ]; then
        return 1
    fi

    median=$(printf '%s' "$speeds" | tr ' ' '\n' | grep -v '^$' | sort -n | awk '{a[NR]=$0} END {if (NR%2==1) print a[(NR+1)/2]; else print int((a[NR/2]+a[NR/2+1])/2)}')
    case "$median" in ''|0|*[!0-9]*) return 1 ;; esac

    printf '%s' "$median"
}

stop_github_proxy() {
    if [ -s "$BASE/github-proxy.pid" ]; then
        old_proxy_pid=$(cat "$BASE/github-proxy.pid")
        case "$old_proxy_pid" in
            ''|*[!0-9]*) ;;
            *) kill "$old_proxy_pid" >/dev/null 2>&1 || true ;;
        esac
    fi
    rm -f "$BASE/github-proxy.pid" "$BASE/github-proxy.json"
    GITHUB_PROXY=''
}

# A live SOCKS listener only proves the tunnel started.  Confirm the tunnel
# actually reaches GitHub before accepting it, otherwise an AAA node that
# cannot route to api.github.com would silently block every upload.
probe_github_proxy() {
    probe_port=$1
    curl --proxy "socks5h://127.0.0.1:$probe_port" -fsS \
        --connect-timeout 8 --max-time 25 -o /dev/null \
        https://api.github.com/rate_limit >/dev/null 2>&1
}

# Walk the ranked endpoints in order, then the remaining reachable nodes, and
# keep the first tunnel that both listens and reaches GitHub.  This is the
# upload retreat: AAA -> BBB -> CCC -> DDD -> EEE -> secondary candidates.
proxy_candidate_uris() {
    secondary=0
    [ -s "$STATE" ] && while IFS='	' read -r cur_host cur_port cur_name cur_latency cur_speed; do
        [ -n "$cur_host" ] || continue
        cur_uri=$(find_uri "$cur_host" "$cur_port" || true)
        [ -n "$cur_uri" ] && printf '%s\n' "$cur_uri"
    done < "$STATE"
    [ -s "$REACHABLE" ] && while IFS='	' read -r cur_host cur_port cur_name cur_latency; do
        [ -n "$cur_host" ] || continue
        is_top_endpoint "$(endpoint_key "$cur_host" "$cur_port")" && continue
        secondary=$((secondary + 1))
        # A long outage must not walk the whole reachable pool one handshake at
        # a time.  The first ranked nodes plus the fastest secondary candidates
        # cover the realistic failure modes.
        [ "$secondary" -le "${CFY_PROXY_FALLBACK_NODES:-12}" ] || break
        cur_uri=$(find_uri "$cur_host" "$cur_port" || true)
        [ -n "$cur_uri" ] && printf '%s\n' "$cur_uri"
    done < "$REACHABLE"
}

prepare_github_proxy() {
    [ -n "$GITHUB_PROXY" ] || [ "$AUTO_PROXY" = 1 ] || return 0
    [ -n "$GITHUB_PROXY" ] && return 0
    proxy_port=${CFY_PROXY_PORT:-10808}
    proxy_pid_file=$BASE/github-proxy.pid
    if [ -s "$proxy_pid_file" ]; then
        proxy_pid=$(cat "$proxy_pid_file")
        if kill -0 "$proxy_pid" 2>/dev/null && probe_github_proxy "$proxy_port"; then
            GITHUB_PROXY="socks5h://127.0.0.1:$proxy_port"
            return 0
        fi
        stop_github_proxy
    fi
    [ -s "$SUBSCRIPTION" ] || return 0
    proxy_cfg=$BASE/github-proxy.json
    attempted=0
    proxy_uri=$(proxy_candidate_uris) || true
    [ -n "$proxy_uri" ] || return 0
    while IFS= read -r candidate_uri; do
        [ -n "$candidate_uri" ] || continue
        candidate_endpoint=$(printf '%s' "$candidate_uri" | sed -e 's#^vless://[^@]*@##' -e 's#[/?].*$##')
        attempted=$((attempted + 1))
        log "proxy candidate #$attempted endpoint=$candidate_endpoint"
        write_xray_config "$candidate_uri" "$proxy_port" "$proxy_cfg" || continue
        "$XRAY" run -c "$proxy_cfg" >/dev/null 2>&1 &
        proxy_pid=$!
        i=0
        listening=0
        while [ "$i" -lt 50 ]; do
            if nc -n -w 1 127.0.0.1 "$proxy_port" </dev/null >/dev/null 2>&1; then
                listening=1
                break
            fi
            sleep 0.1
            i=$((i + 1))
        done
        if [ "$listening" -eq 1 ] && probe_github_proxy "$proxy_port"; then
            printf '%s' "$proxy_pid" > "$proxy_pid_file"
            GITHUB_PROXY="socks5h://127.0.0.1:$proxy_port"
            log "proxy ready via $candidate_endpoint after $attempted candidate(s)"
            return 0
        fi
        kill "$proxy_pid" >/dev/null 2>&1 || true
        wait "$proxy_pid" 2>/dev/null || true
    done <<EOF
$proxy_uri
EOF
    log "no usable GitHub proxy after $attempted candidate(s)"
    rm -f "$proxy_cfg"
    return 0
}

canonical() {
    printf '%s' "$1" | sed -e 's/^[Aa][Aa][Aa]-//' -e 's/^[Bb][Bb][Bb]-//' \
        -e 's/^[Cc][Cc][Cc]-//' -e 's/^[Dd][Dd][Dd]-//' -e 's/^[Ee][Ee][Ee]-//'
}

is_top_endpoint() {
    key=$1
    [ -f "$BASE/top.keys" ] && grep -F -x "$key" "$BASE/top.keys" >/dev/null 2>&1
}

build_output() {
    ranked=$1; output=$BASE/ip.txt
    : > "$BASE/top.keys"
    : > "$output.tmp"
    rank=0
    while IFS='	' read -r host port name latency speed; do
        [ -n "$host" ] || continue
        rank=$((rank + 1))
        case "$rank" in 1) label=AAA ;; 2) label=BBB ;; 3) label=CCC ;; 4) label=DDD ;; 5) label=EEE ;; esac
        base=$(canonical "$name")
        printf '%s:%s#%s-%s\n' "$host" "$port" "$label" "$base" >> "$output.tmp"
        endpoint_key "$host" "$port" >> "$BASE/top.keys"
    done < "$ranked"
    secondary_rank=0
    while IFS='	' read -r host port name latency; do
        [ -n "$host" ] || continue
        key=$(endpoint_key "$host" "$port")
        is_top_endpoint "$key" && continue
        secondary_rank=$((secondary_rank + 1))
        printf '%s:%s#%s.%s\n' "$host" "$port" "$secondary_rank" "$name" >> "$output.tmp"
    done < "$REACHABLE"
    mv "$output.tmp" "$output"
}

github_put() {
    file=$1; path=$2; message=$3
    [ -n "$TOKEN" ] || return 1
    metadata=$BASE/metadata.json
    headers="$BASE/headers.txt"
    encoded=$(base64 "$file" | tr -d '\n')
    body=$BASE/put.json
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        sha=''
        if github_curl -fsSL -H "Authorization: Bearer $TOKEN" -H 'Accept: application/vnd.github+json' "$API/$path?ref=$BRANCH" -o "$metadata"; then
            sha=$(sed -n 's/.*"sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$metadata" | head -n 1)
        fi
        printf '{"message":"%s","content":"%s"' "$(json_escape "$message")" "$encoded" > "$body"
        [ -n "$sha" ] && printf ',"sha":"%s"' "$sha" >> "$body"
        printf ',"branch":"%s"}\n' "$(json_escape "$BRANCH")" >> "$body"
        if github_curl -fsSL -X PUT -H "Authorization: Bearer $TOKEN" -H 'Accept: application/vnd.github+json' \
            -H 'Content-Type: application/json' --data-binary "@$body" "$API/$path" -o "$headers"; then
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 2
    done
    return 1
}

write_status() {
    mode=$1; reachable_count=$2; speed_count=$3; aggregate_hash=$4
    status=$BASE/device-status.json
    now=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    cat > "$status" <<EOF
{"device_id":"$(json_escape "$DEVICE_ID")","updated_at":"$now","aggregate_sha256":"$aggregate_hash","mode":"$mode","reachable":$reachable_count,"speed_tested":$speed_count,"worker_version":"1.0","publish":{"ip_ok":true}}
EOF
    github_put "$status" device-status.json "Android optimizer heartbeat ($mode)"
}

full_measurement() {
    candidates=$BASE/candidates.tsv
    awk -F '\t' '$3 ~ /(^|[-_])(SG|JP)([0-9]|$)/ {print}' "$REACHABLE" > "$candidates"
    : > "$BASE/measured.tsv"
    count=0
    total_candidates=$(wc -l < "$candidates" | tr -d ' ')
    offset=0
    while [ "$offset" -lt "$total_candidates" ] && [ "$count" -lt 5 ]; do
        batch_start=$((offset + 1))
        batch_end=$((offset + 10))
        sed -n "${batch_start},${batch_end}p" "$candidates" > "$BASE/candidate-batch.tsv"
        while IFS='	' read -r host port name latency; do
            [ -n "$host" ] || continue
            uri=$(find_uri "$host" "$port" || true)
            [ -n "$uri" ] || continue
            speed=$(speed_probe "$uri" || true)
            if [ -n "$speed" ]; then
                printf '%s\t%s\t%s\t%s\t%s\n' "$host" "$port" "$name" "$latency" "$speed" >> "$BASE/measured.tsv"
                count=$((count + 1))
            fi
        done < "$BASE/candidate-batch.tsv"
        offset=$((offset + 10))
    done
    sort -t '	' -k5,5nr -k4,4n "$BASE/measured.tsv" | head -n 5 > "$BASE/ranked.tsv"
    if [ "$count" -lt 5 ]; then
        log "full ranking kept unchanged: only $count non-zero speed tests completed"
        printf '%s' "$count"
        return 0
    fi
    cp "$BASE/ranked.tsv" "$STATE"
    build_output "$BASE/ranked.tsv"
    printf '%s' "$count"
}

incremental_measurement() {
    old=$BASE/old.tsv
    cp "$STATE" "$old"

    # 步骤 1: 验证前 5 名存活性
    log 'incremental: verify top 5 liveness'
    : > "$BASE/alive.tsv"
    while IFS='	' read -r host port name latency speed; do
        # TCP ping 验证
        if timeout 3 nc -4 -n -w 3 "$host" "$port" </dev/null >/dev/null 2>&1; then
            printf '%s\t%s\t%s\t%s\t%s\n' "$host" "$port" "$name" "$latency" "$speed" >> "$BASE/alive.tsv"
            log "incremental: $host:$port alive"
        else
            log "incremental: $host:$port dead, removed"
        fi
    done < "$old"

    alive_count=$(wc -l < "$BASE/alive.tsv" | tr -d ' ')
    if [ "$alive_count" -lt 3 ]; then
        log "incremental: only $alive_count nodes alive, triggering full"
        return 1
    fi

    # 步骤 2: 对存活节点重新测速
    : > "$BASE/remeasured.tsv"
    speed_count=0
    while IFS='	' read -r host port name latency old_speed; do
        uri=$(find_uri "$host" "$port" || true)
        if [ -n "$uri" ]; then
            new_speed=$(speed_probe "$uri" || true)
            if [ -n "$new_speed" ]; then
                speed_count=$((speed_count + 1))
                printf '%s\t%s\t%s\t%s\t%s\n' "$host" "$port" "$name" "$latency" "$new_speed" >> "$BASE/remeasured.tsv"
                log "incremental: $host:$port remeasured speed=$new_speed"
            else
                # 测速失败，保留旧速率
                printf '%s\t%s\t%s\t%s\t%s\n' "$host" "$port" "$name" "$latency" "$old_speed" >> "$BASE/remeasured.tsv"
                log "incremental: $host:$port speed test failed, keep old speed"
            fi
        else
            # URI 未找到，保留旧速率
            printf '%s\t%s\t%s\t%s\t%s\n' "$host" "$port" "$name" "$latency" "$old_speed" >> "$BASE/remeasured.tsv"
            log "incremental: $host:$port uri not found, keep old speed"
        fi
    done < "$BASE/alive.tsv"

    # 步骤 3: 重新排序
    sort -t '	' -k5,5nr -k4,4n "$BASE/remeasured.tsv" | head -n 5 > "$BASE/ranked.tsv"
    cp "$BASE/ranked.tsv" "$STATE"
    build_output "$BASE/ranked.tsv"
    printf '%s' "$speed_count"
}

run_once() {
    had_state=0; [ -s "$STATE" ] && had_state=1
    if [ "$had_state" -eq 0 ] && [ -n "$BOOTSTRAP_PROXY" ]; then
        GITHUB_PROXY=$BOOTSTRAP_PROXY
    fi
    fetch_subscription || { log 'subscription fetch failed'; return 1; }
    if [ "$had_state" -eq 1 ]; then
        prepare_github_proxy
    fi
    fetch_aggregate || { log 'aggregate fetch failed'; return 1; }
    aggregate_hash=$(sha256sum "$AGGREGATE" | awk '{print $1}')
    reachable_count=$(probe_aggregate)
    [ "$reachable_count" -gt 0 ] || { log 'no reachable nodes'; return 1; }
    old_hash=''; [ -f "$BASE/aggregate.sha256" ] && old_hash=$(cat "$BASE/aggregate.sha256")
    measurement_changed=1
    if [ -r "$MEASUREMENT_FILE" ] && [ "$(cat "$MEASUREMENT_FILE")" = "$MEASUREMENT_VERSION" ]; then
        measurement_changed=0
    fi
    run_mode=incremental
    if [ "$aggregate_hash" != "$old_hash" ] || [ "$measurement_changed" -eq 1 ] || [ ! -s "$STATE" ] || [ "$(wc -l < "$STATE")" -lt 5 ]; then
        run_mode=full
        speed_count=$(full_measurement)
        [ "$speed_count" -ge 5 ] || return 1
    else
        speed_count=$(incremental_measurement)
        if [ "$?" -ne 0 ]; then
            log 'incremental failed, fallback to full'
            run_mode=full
            speed_count=$(full_measurement)
            [ "$speed_count" -ge 5 ] || return 1
        fi
    fi
    printf '%s' "$aggregate_hash" > "$BASE/aggregate.sha256"
    if [ "$run_mode" = full ]; then
        printf '%s' "$MEASUREMENT_VERSION" > "$MEASUREMENT_FILE"
    fi
    # The first complete pass now has a measured rank-1 node.  Start the
    # device Xray proxy from that node before committing to GitHub.  Drop the
    # previous tunnel first: it still points at the node the new ranking may
    # have just evicted, and reusing it would publish a stale ranking.
    if [ "$run_mode" = full ]; then
        stop_github_proxy
        prepare_github_proxy
    fi
    github_put "$BASE/ip.txt" ip.txt "Android optimizer update ($run_mode)" || {
        log 'ip.txt upload failed; heartbeat suppressed'
        return 1
    }
    write_status "$run_mode" "$reachable_count" "$speed_count" "$aggregate_hash" || {
        log 'heartbeat upload failed'
        return 1
    }
    log "$run_mode complete: reachable=$reachable_count speed_tests=$speed_count"
    return 0
}

case "${1:-daemon}" in
    once) run_once_locked ;;
    daemon)
        while :; do
            run_once_locked || true
            sleep "$POLL_SECONDS"
        done
        ;;
    *) echo "usage: $0 [once|daemon]" >&2; exit 2 ;;
esac
