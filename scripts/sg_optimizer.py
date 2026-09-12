#!/usr/bin/env python3
"""Measure SG VLESS/XHTTP nodes and publish only sg-ranking.json."""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import os
import socket
import statistics
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
XRAY = os.environ.get("XRAY_BIN", "/usr/local/bin/xray")
SUB_URL = os.environ.get("SG_SUB_URL", "").strip()
TEST_URL = os.environ.get(
    "SG_TEST_URL",
    "https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip",
)
TEST_BYTES = int(os.environ.get("SG_TEST_BYTES", "8388608"))
RANKING_FILE = ROOT / "sg-ranking.json"
STATE_FILE = Path(os.environ.get("SG_STATE_FILE", "/var/lib/xray-sg-optimizer/state.json"))
TCP_ATTEMPTS = 3
TCP_TIMEOUT = 3.0
PROXY_TIMEOUT = 8
SPEED_TIMEOUT = 28
TOP_TCP = 10


def fetch_subscription() -> str:
    if not SUB_URL:
        raise RuntimeError("SG_SUB_URL is not configured")
    req = urllib.request.Request(SUB_URL, headers={"User-Agent": "xray-sg-optimizer/1.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read().decode("utf-8", "replace")
    compact = "".join(raw.split())
    try:
        decoded = base64.b64decode(compact + "=" * (-len(compact) % 4)).decode("utf-8", "replace")
        if "vless://" in decoded or "vmess://" in decoded:
            return decoded
    except Exception:
        pass
    return raw


def parse_vless(line: str) -> dict | None:
    line = line.strip()
    if not line.lower().startswith("vless://"):
        return None
    try:
        parsed = urllib.parse.urlsplit(line)
        if not parsed.hostname or not parsed.port or not parsed.username:
            return None
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        extra = {}
        if query.get("extra"):
            extra = json.loads(query["extra"][0])
        name = urllib.parse.unquote(parsed.fragment or "")
        if "SG" not in name.upper():
            return None
        return {
            "uri": line,
            "name": name or f"SG-{parsed.hostname}:{parsed.port}",
            "address": parsed.hostname,
            "port": parsed.port,
            "uuid": urllib.parse.unquote(parsed.username),
            "encryption": query.get("encryption", ["none"])[0] or "none",
            "security": query.get("security", ["none"])[0] or "none",
            "network": query.get("type", ["raw"])[0] or "raw",
            "host": query.get("host", [""])[0],
            "sni": query.get("sni", [""])[0],
            "fp": query.get("fp", [""])[0],
            "path": query.get("path", ["/"])[0] or "/",
            "mode": query.get("mode", [""])[0],
            "alpn": query.get("alpn", [""])[0],
            "extra": extra,
        }
    except (ValueError, json.JSONDecodeError, KeyError):
        return None


def parse_candidates(text: str) -> list[dict]:
    seen = set()
    candidates = []
    for line in text.splitlines():
        node = parse_vless(line)
        if not node:
            continue
        key = (node["address"], node["port"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(node)
    return candidates


def tcp_probe(node: dict) -> dict:
    samples = []
    for _ in range(TCP_ATTEMPTS):
        started = time.perf_counter()
        try:
            with socket.create_connection((node["address"], node["port"]), TCP_TIMEOUT):
                samples.append((time.perf_counter() - started) * 1000)
        except OSError:
            pass
    result = dict(node)
    result["tcp_ms"] = round(statistics.median(samples), 2) if samples else None
    return result


def probe_tcp_many(nodes: list[dict]) -> list[dict]:
    workers = min(32, max(1, len(nodes)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(tcp_probe, nodes))


def xray_config(node: dict, socks_port: int) -> dict:
    tls = {}
    if node["sni"]:
        tls["serverName"] = node["sni"]
    if node["fp"]:
        tls["fingerprint"] = node["fp"]
    if node["alpn"]:
        tls["alpn"] = [x for x in node["alpn"].split(",") if x]
    stream = {"network": node["network"], "security": node["security"]}
    if tls:
        stream["tlsSettings"] = tls
    if node["network"] == "xhttp":
        xhttp = {"path": node["path"]}
        if node["host"]:
            xhttp["host"] = node["host"]
        if node["mode"]:
            xhttp["mode"] = node["mode"]
        if node["extra"]:
            # XHTTP share links carry the provider-supplied JSON as one
            # nested extra object.  Expanding it at the xhttpSettings level
            # starts Xray but produces an unusable transport configuration.
            xhttp["extra"] = node["extra"]
        stream["xhttpSettings"] = xhttp
    elif node["network"] == "ws":
        stream["wsSettings"] = {"path": node["path"], "headers": {"Host": node["host"]}} if node["host"] else {"path": node["path"]}
    return {
        "log": {"loglevel": "error"},
        "inbounds": [{"listen": "127.0.0.1", "port": socks_port, "protocol": "socks", "settings": {"udp": False}}],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {"vnext": [{"address": node["address"], "port": node["port"], "users": [{"id": node["uuid"], "encryption": node["encryption"]}]}]},
                "streamSettings": stream,
            },
            {"tag": "direct", "protocol": "freedom"},
        ],
    }


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def speed_probe(node: dict) -> dict:
    result = dict(node)
    result["speed_bps"] = None
    result["speed_bytes"] = 0
    result["speed_seconds"] = None
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="xray-sg-") as tmp:
        config_path = Path(tmp) / "config.json"
        config_path.write_text(json.dumps(xray_config(node, port)), encoding="utf-8")
        try:
            proc = subprocess.Popen([XRAY, "run", "-c", str(config_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), 0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                return result
            cmd = [
                "curl", "--silent", "--show-error", "--fail", "--location",
                "--proxy", f"socks5h://127.0.0.1:{port}",
                "--connect-timeout", str(PROXY_TIMEOUT), "--max-time", str(SPEED_TIMEOUT),
                "--range", f"0-{TEST_BYTES - 1}",
                "-o", "/dev/null", "-w", "%{size_download}\t%{time_total}",
                TEST_URL,
            ]
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=SPEED_TIMEOUT + 5)
            if completed.returncode == 0:
                fields = completed.stdout.strip().split("\t")
                if len(fields) == 2:
                    size = float(fields[0])
                    seconds = float(fields[1])
                    if size > 0 and seconds > 0:
                        result["speed_bytes"] = int(size)
                        result["speed_seconds"] = round(seconds, 3)
                        result["speed_bps"] = round(size / seconds, 2)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (UnboundLocalError, OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except (UnboundLocalError, OSError):
                    pass
    return result


def fingerprint(nodes: list[dict]) -> str:
    payload = "\n".join(sorted(node["uri"] for node in nodes)).encode()
    return hashlib.sha256(payload).hexdigest()


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(STATE_FILE)


def ranked_slots(nodes: list[dict]) -> list[dict]:
    working = [node for node in nodes if node.get("speed_bps")]
    working.sort(key=lambda x: (-x["speed_bps"], x.get("tcp_ms") or 999999))
    return working[:5]


def speed_key(node: dict) -> tuple[float, float]:
    return (-(node.get("speed_bps") or 0), node.get("tcp_ms") or 999999)


def restore_slot(item: dict, by_key: dict[tuple[str, int], dict]) -> dict | None:
    try:
        key = (str(item["address"]), int(item["port"]))
    except (KeyError, TypeError, ValueError):
        return None
    node = by_key.get(key)
    if not node:
        return None
    for field in ("tcp_ms", "speed_bps", "speed_bytes", "speed_seconds", "measured_at"):
        if field in item:
            node[field] = item[field]
    return node


def incremental_slots(candidates: list[dict], old_state: dict) -> list[dict] | None:
    by_key = {(node["address"], node["port"]): node for node in candidates}
    old_slots = old_state.get("slots", [])
    if not isinstance(old_slots, list) or len(old_slots) < 5:
        return None
    slots = []
    for item in old_slots[:5]:
        if not isinstance(item, dict):
            return None
        node = restore_slot(item, by_key)
        if node is None:
            return None
        slots.append(node)

    now = datetime.now(timezone.utc).isoformat()
    challengers = []
    for challenger in slots[3:5]:
        original = dict(challenger)
        probed = tcp_probe(challenger)
        if probed.get("tcp_ms") is not None:
            measured = speed_probe(probed)
            if measured.get("speed_bps"):
                measured["measured_at"] = now
                challenger = measured
            else:
                challenger = original
        else:
            challenger = original
        challengers.append(challenger)

    # Sort once after both challenges. This keeps the displaced nodes stable
    # when both challengers beat incumbents in the same run.
    return sorted(slots[:3] + challengers, key=speed_key)[:5]


def ranking_payload(slots: list[dict], candidate_hash: str, full: bool) -> dict:
    labels = ["AAA", "BBB", "CCC", "", ""]
    output = []
    for rank, node in enumerate(slots[:5], 1):
        output.append({
            "rank": rank,
            "name": f"{labels[rank - 1] + '-' if labels[rank - 1] else ''}{node['name']}",
            "source_name": node["name"],
            "address": node["address"],
            "port": node["port"],
            "tcp_ms": node.get("tcp_ms"),
            "speed_bps": node.get("speed_bps"),
            "measured_at": node.get("measured_at"),
        })
    return {
        "version": 1,
        "region": "SG",
        "method": "TCP median of 3; concurrent top 10; Xray proxy download 8 MiB",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_fingerprint": candidate_hash,
        "full_measurement": full,
        "slots": output,
    }


def main():
    candidates = parse_candidates(fetch_subscription())
    if not candidates:
        raise RuntimeError("no SG VLESS nodes found in subscription")
    candidate_hash = fingerprint(candidates)
    old_state = load_state()
    full = old_state.get("candidate_fingerprint") != candidate_hash or len(old_state.get("slots", [])) < 5
    if full:
        probed = probe_tcp_many(candidates)
        tcp_ok = sorted([n for n in probed if n.get("tcp_ms") is not None], key=lambda n: n["tcp_ms"])[:TOP_TCP]
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, max(1, len(tcp_ok)))) as pool:
            measured = list(pool.map(speed_probe, tcp_ok))
        now = datetime.now(timezone.utc).isoformat()
        for node in measured:
            node["measured_at"] = now
        slots = ranked_slots(measured)
        if len(slots) < 5:
            raise RuntimeError(f"only {len(slots)} SG nodes completed proxy speed tests")
    else:
        slots = incremental_slots(candidates, old_state)
        if slots is None:
            full = True
            old_state = {}
            probed = probe_tcp_many(candidates)
            tcp_ok = sorted([n for n in probed if n.get("tcp_ms") is not None], key=lambda n: n["tcp_ms"])[:TOP_TCP]
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, max(1, len(tcp_ok)))) as pool:
                measured = list(pool.map(speed_probe, tcp_ok))
            now = datetime.now(timezone.utc).isoformat()
            for node in measured:
                node["measured_at"] = now
            slots = ranked_slots(measured)
            if len(slots) < 5:
                raise RuntimeError(f"only {len(slots)} SG nodes completed proxy speed tests")
    payload = ranking_payload(slots, candidate_hash, full)
    save_state({"candidate_fingerprint": candidate_hash, "slots": slots, "updated_at": payload["generated_at"]})
    RANKING_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main_full(candidates, candidate_hash):
    old = load_state()
    old["candidate_fingerprint"] = ""
    save_state(old)
    return main()


if __name__ == "__main__":
    main()
