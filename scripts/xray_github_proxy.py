#!/usr/bin/env python3
"""Run a LAN SOCKS5 proxy through the current SG rank-1 Xray node."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from sg_optimizer import fetch_subscription, parse_candidates, xray_config  # noqa: E402


XRAY = os.environ.get("XRAY_BIN", "/usr/local/bin/xray")
RANKING_FILE = ROOT / "sg-ranking.json"
STATE_DIR = Path(os.environ.get("SG_STATE_DIR", "/var/lib/xray-sg-optimizer"))
CONFIG_FILE = STATE_DIR / "github-proxy.json"
LISTEN = os.environ.get("GITHUB_PROXY_LISTEN", "0.0.0.0")
SOCKS_PORT = int(os.environ.get("GITHUB_PROXY_PORT", "10808"))


def load_top_slot() -> tuple[str, int, str]:
    data = json.loads(RANKING_FILE.read_text(encoding="utf-8"))
    slots = data.get("slots", [])
    if not isinstance(slots, list) or not slots:
        raise RuntimeError("sg-ranking.json has no rank-1 slot")
    slot = slots[0]
    address = str(slot.get("address", "")).strip().strip("[]")
    port = int(slot.get("port"))
    name = str(slot.get("source_name") or slot.get("name") or "SG-rank-1").strip()
    if not address or not 1 <= port <= 65535:
        raise RuntimeError("sg-ranking.json rank-1 slot is invalid")
    return address, port, name


def select_node(address: str, port: int) -> dict:
    candidates = parse_candidates(fetch_subscription())
    for candidate in candidates:
        if candidate["address"].lower() == address.lower() and candidate["port"] == port:
            return candidate
    raise RuntimeError(f"rank-1 node {address}:{port} is absent from the current subscription")


def write_config(node: dict) -> None:
    config = xray_config(node, SOCKS_PORT)
    config["inbounds"] = [
        {
            "listen": LISTEN,
            "port": SOCKS_PORT,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False},
        }
    ]
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(CONFIG_FILE)


def run(config_file: Path) -> None:
    os.execv(XRAY, [XRAY, "run", "-c", str(config_file)])


def main() -> None:
    try:
        address, port, source_name = load_top_slot()
        node = select_node(address, port)
        write_config(node)
        print(
            f"using previous rank-1 node {source_name} at {address}:{port}; "
            f"LAN SOCKS5 listening on {LISTEN}:{SOCKS_PORT}",
            file=sys.stderr,
            flush=True,
        )
        run(CONFIG_FILE)
    except Exception as exc:
        if CONFIG_FILE.exists():
            print(f"using last valid proxy configuration: {type(exc).__name__}", file=sys.stderr)
            run(CONFIG_FILE)
        raise


if __name__ == "__main__":
    main()
