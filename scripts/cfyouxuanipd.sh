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
TEST_BYTES=${CFY_TEST_BYTES:-8388608}
POLL_SECONDS=${CFY_POLL_SECONDS:-3600}
TCP_TIMEOUT=${CFY_TCP_TIMEOUT:-4}
STATE=$BASE/state.tsv
AGGREGATE=$BASE/ip.aggregate.txt
REACHABLE=$BASE/reachable.tsv
SUBSCRIPTION=$BASE/subscription.txt
LOG=$BASE/worker.log
API=https://api.github.com/repos/$REPO/contents
RAW=https://raw.githubusercontent.com/$REPO/$BRANCH/ip.aggregate.txt
DEVICE_ID=${CFY_DEVICE_ID:-unknown}

mkdir -p "$BASE"
umask 077

log() {
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG"
    tail -c 262144 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
}

die() { log "ERROR: $*"; exit 1; }

url_decode() {
    # Subscription links use these escapes for JSON and paths.  Values are
    # otherwise kept untouched so names and hosts remain readable.
    printf '%s' "$1" | sed \
        -e 's/%7B/{/g' -e 's/%7b/{/g' -e 's/%7D/}/g' -e 's/%7d/}/g' \
        -e 's/%22/"/g' -e 's/%3A/:/g' -e 's/%3a/:/g' -e 's/%2C/,/g' -e 's/%2c/,/g' \
        -e 's/%2F/\\//g' -e 's/%2f/\\//g' -e 's/%3F/?/g' -e 's/%3f/?/g' \
        -e 's/%3D/=/g' -e 's/%3d/=/g' -e 's/%26/\\&/g' -e 's/%25/%/g'
}

param() {
    printf '%s' "$1" | tr '&' '\n' | sed -n "s/^$2=//p" | head -n 1
}

json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

endpoint_key() { printf '%s:%s' "$1" "$2"; }

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
    if ! curl -fsSL --connect-timeout 20 --max-time 90 "$RAW?ts=$(date +%s)" -o "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    [ -s "$tmp" ] || { rm -f "$tmp"; return 1; }
    mv "$tmp" "$AGGREGATE"
}

probe_aggregate() {
    : > "$REACHABLE.tmp"
    while IFS= read -r line || [ -n "$line" ]; do
        line=$(printf '%s' "$line" | tr -d '\r')
        [ -n "$line" ] || continue
        case "$line" in *#*) ;; *) continue ;; esac
        address=${line%%#*}; name=${line#*#}
        case "$address" in *:*) ;; *) continue ;; esac
        host=${address%:*}; port=${address##*:}
        case "$port" in ''|*[!0-9]*) continue ;; esac
        if latency=$(tcp_ms "$host" "$port"); then
            printf '%s\t%s\t%s\t%s\n' "$host" "$port" "$name" "$latency" >> "$REACHABLE.tmp"
        fi
    done < "$AGGREGATE"
    sort -t '	' -k4,4n "$REACHABLE.tmp" > "$REACHABLE"
    rm -f "$REACHABLE.tmp"
    wc -l < "$REACHABLE" | tr -d ' '
}

fetch_subscription() {
    [ -n "$SUB_URL" ] || return 1
    raw=$BASE/subscription.raw
    curl -fsSL --connect-timeout 20 --max-time 90 "$SUB_URL" -o "$raw" || return 1
    first=$(head -n 1 "$raw")
    case "$first" in
        vless://*|vmess://*) cp "$raw" "$SUBSCRIPTION" ;;
        *) base64 -d "$raw" > "$SUBSCRIPTION" 2>/dev/null || cp "$raw" "$SUBSCRIPTION" ;;
    esac
    [ -s "$SUBSCRIPTION" ]
}

find_uri() {
    host=$1; port=$2
    # The aggregate contains the same endpoint and region suffix as the
    # subscription.  Match the endpoint directly to preserve its UUID and
    # XHTTP transport parameters.
    grep -F "@$host:$port?" "$SUBSCRIPTION" | head -n 1 || \
        grep -F "@[$host]:$port?" "$SUBSCRIPTION" | head -n 1
}

write_xray_config() {
    uri=$1; socks=$2; output=$3
    auth=${uri#vless://}; uuid=${auth%@*}; rest=${auth#*@}
    endpoint=${rest%%\?*}; queryfrag=${rest#*\?}; query=${queryfrag%%#*}
    host=${endpoint%:*}; port=${endpoint##*:}
    security=$(param "$query" security); [ -n "$security" ] || security=none
    network=$(param "$query" type); [ -n "$network" ] || network=tcp
    sni=$(url_decode "$(param "$query" sni)")
    fp=$(url_decode "$(param "$query" fp)")
    alpn=$(url_decode "$(param "$query" alpn)")
    path=$(url_decode "$(param "$query" path)"); [ -n "$path" ] || path=/
    transport_host=$(url_decode "$(param "$query" host)")
    mode=$(url_decode "$(param "$query" mode)")
    extra=$(url_decode "$(param "$query" extra)")
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
        [ -n "$mode" ] && xhttp="$xhttp,\"mode\":\"$(json_escape "$mode")\""
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
{"log":{"loglevel":"error"},"inbounds":[{"listen":"127.0.0.1","port":$socks,"protocol":"socks","settings":{"udp":false}}],"outbounds":[{"protocol":"vless","settings":{"vnext":[{"address":"$(json_escape "$host")","port":$port,"users":[{"id":"$(json_escape "$uuid")","encryption":"none"}]}]},"streamSettings":{$stream}},{"protocol":"freedom"}]}
EOF
}

speed_probe() {
    uri=$1
    port=$((18080 + ($$ % 1000)))
    cfg=$BASE/xray-speed.json
    write_xray_config "$uri" "$port" "$cfg" || return 1
    "$XRAY" run -c "$cfg" >/dev/null 2>&1 &
    xpid=$!
    ready=0
    i=0
    while [ "$i" -lt 50 ]; do
        if nc -n -w 1 127.0.0.1 "$port" </dev/null >/dev/null 2>&1; then ready=1; break; fi
        sleep 0.1; i=$((i + 1))
    done
    result=''
    if [ "$ready" -eq 1 ]; then
        result=$(curl -fsSL --proxy "socks5h://127.0.0.1:$port" --connect-timeout 8 --max-time 35 --range "0-$((TEST_BYTES - 1))" -o /dev/null -w '%{size_download}	%{time_total}' "$TEST_URL" 2>/dev/null || true)
    fi
    kill "$xpid" >/dev/null 2>&1 || true
    wait "$xpid" 2>/dev/null || true
    rm -f "$cfg"
    size=${result%%	*}; seconds=${result#*	}
    case "$size" in ''|*[!0-9.]*) return 1 ;; esac
    case "$seconds" in ''|0|*[!0-9.]*) return 1 ;; esac
    bps=$(awk -v s="$size" -v t="$seconds" 'BEGIN { if (t > 0) printf "%.0f", s/t; }')
    [ -n "$bps" ] || return 1
    printf '%s' "$bps"
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
    while IFS='	' read -r host port name latency; do
        [ -n "$host" ] || continue
        key=$(endpoint_key "$host" "$port")
        is_top_endpoint "$key" && continue
        printf '%s:%s#%s\n' "$host" "$port" "$name" >> "$output.tmp"
    done < "$REACHABLE"
    mv "$output.tmp" "$output"
}

github_put() {
    file=$1; path=$2; message=$3
    [ -n "$TOKEN" ] || return 1
    metadata=$BASE/metadata.json
    headers="$BASE/headers.txt"
    if ! curl -fsSL -H "Authorization: Bearer $TOKEN" -H 'Accept: application/vnd.github+json' "$API/$path?ref=$BRANCH" -o "$metadata"; then
        rm -f "$metadata"
        return 1
    fi
    sha=$(sed -n 's/.*"sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$metadata" | head -n 1)
    encoded=$(base64 "$file" | tr -d '\n')
    body=$BASE/put.json
    printf '{"message":"%s","content":"%s"' "$(json_escape "$message")" "$encoded" > "$body"
    [ -n "$sha" ] && printf ',"sha":"%s"' "$sha" >> "$body"
    printf ',"branch":"%s"}\n' "$(json_escape "$BRANCH")" >> "$body"
    curl -fsSL -X PUT -H "Authorization: Bearer $TOKEN" -H 'Accept: application/vnd.github+json' \
        -H 'Content-Type: application/json' --data-binary "@$body" "$API/$path" -o "$headers"
}

write_status() {
    mode=$1; reachable_count=$2; speed_count=$3; aggregate_hash=$4
    status=$BASE/device-status.json
    now=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    cat > "$status" <<EOF
{"device_id":"$(json_escape "$DEVICE_ID")","updated_at":"$now","aggregate_sha256":"$aggregate_hash","mode":"$mode","reachable":$reachable_count,"speed_tested":$speed_count,"worker_version":"1.0"}
EOF
    github_put "$status" device-status.json "Android optimizer heartbeat ($mode)"
}

full_measurement() {
    candidates=$BASE/candidates.tsv
    awk -F '	' '$3 ~ /(^|[-_])(SG|JP)([0-9]|$)/ {print}' "$REACHABLE" | head -n 10 > "$candidates"
    : > "$BASE/measured.tsv"
    count=0
    while IFS='	' read -r host port name latency; do
        [ -n "$host" ] || continue
        uri=$(find_uri "$host" "$port" || true)
        [ -n "$uri" ] || continue
        speed=$(speed_probe "$uri" || true)
        if [ -n "$speed" ]; then
            printf '%s\t%s\t%s\t%s\t%s\n' "$host" "$port" "$name" "$latency" "$speed" >> "$BASE/measured.tsv"
            count=$((count + 1))
        fi
    done < "$candidates"
    sort -t '	' -k5,5nr -k4,4n "$BASE/measured.tsv" | head -n 5 > "$BASE/ranked.tsv"
    cp "$BASE/ranked.tsv" "$STATE"
    build_output "$BASE/ranked.tsv"
    printf '%s' "$count"
}

incremental_measurement() {
    old=$BASE/old.tsv
    cp "$STATE" "$old"
    : > "$BASE/challengers.tsv"
    rank=0
    speed_count=0
    while IFS='	' read -r host port name latency speed; do
        rank=$((rank + 1))
        if [ "$rank" -ge 4 ]; then
            uri=$(find_uri "$host" "$port" || true)
            new_speed=''
            [ -n "$uri" ] && new_speed=$(speed_probe "$uri" || true)
            [ -n "$new_speed" ] && speed_count=$((speed_count + 1)) || new_speed=$speed
            printf '%s\t%s\t%s\t%s\t%s\n' "$host" "$port" "$name" "$latency" "$new_speed" >> "$BASE/challengers.tsv"
        else
            printf '%s\t%s\t%s\t%s\t%s\n' "$host" "$port" "$name" "$latency" "$speed" >> "$BASE/challengers.tsv"
        fi
    done < "$old"
    # A challenger can enter the top five only by beating a previous top-three
    # speed.  Sorting all five also handles both challengers winning together.
    top3=$(head -n 3 "$old" | awk -F '	' '{print $5}')
    sort -t '	' -k5,5nr -k4,4n "$BASE/challengers.tsv" | head -n 5 > "$BASE/ranked.tsv"
    cp "$BASE/ranked.tsv" "$STATE"
    build_output "$BASE/ranked.tsv"
    printf '%s' "$speed_count"
}

run_once() {
    fetch_aggregate || { log 'aggregate fetch failed'; return 1; }
    fetch_subscription || { log 'subscription fetch failed'; return 1; }
    aggregate_hash=$(sha256sum "$AGGREGATE" | awk '{print $1}')
    reachable_count=$(probe_aggregate)
    [ "$reachable_count" -gt 0 ] || { log 'no reachable nodes'; return 1; }
    old_hash=''; [ -f "$BASE/aggregate.sha256" ] && old_hash=$(cat "$BASE/aggregate.sha256")
    mode=incremental
    if [ "$aggregate_hash" != "$old_hash" ] || [ ! -s "$STATE" ] || [ "$(wc -l < "$STATE")" -lt 5 ]; then
        mode=full
        speed_count=$(full_measurement)
    else
        speed_count=$(incremental_measurement)
    fi
    printf '%s' "$aggregate_hash" > "$BASE/aggregate.sha256"
    github_put "$BASE/ip.txt" ip.txt "Android optimizer update ($mode)" || log 'ip.txt upload failed'
    write_status "$mode" "$reachable_count" "$speed_count" "$aggregate_hash" || log 'heartbeat upload failed'
    log "$mode complete: reachable=$reachable_count speed_tests=$speed_count"
    return 0
}

case "${1:-daemon}" in
    once) run_once ;;
    daemon)
        while :; do
            run_once || true
            sleep "$POLL_SECONDS"
        done
        ;;
    *) echo "usage: $0 [once|daemon]" >&2; exit 2 ;;
esac
