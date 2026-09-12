#!/usr/bin/env python3
"""Put the locally measured SG ranking at the top of ip.txt."""

import json
import re
from pathlib import Path

RANKING = Path("sg-ranking.json")
IP_FILE = Path("ip.txt")
ENDPOINT_RE = re.compile(r"^(?P<address>\[[^]]+\]|[^:]+):(?P<port>\d+)#")


def load_ranking():
    if not RANKING.exists():
        return []
    try:
        data = json.loads(RANKING.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    slots = data.get("slots", []) if isinstance(data, dict) else []
    result = []
    seen = set()
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        address = str(slot.get("address", "")).strip().strip("[]")
        port = str(slot.get("port", "")).strip()
        name = str(slot.get("name", "")).strip()
        if not address or not port.isdigit() or not name:
            continue
        key = (address, port)
        if key in seen:
            continue
        seen.add(key)
        result.append(f"{address}:{port}#{name}")
        if len(result) == 5:
            break
    return result


def endpoint(line):
    match = ENDPOINT_RE.match(line.strip())
    return (match.group("address").strip("[]"), match.group("port")) if match else None


def main():
    aggregate = [line.strip() for line in IP_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    ranked = load_ranking()
    ranked_endpoints = {endpoint(line) for line in ranked}
    ranked_endpoints.discard(None)
    remainder = [line for line in aggregate if endpoint(line) not in ranked_endpoints]
    IP_FILE.write_text("\n".join(ranked + remainder) + "\n", encoding="utf-8")
    print(f"applied {len(ranked)} SG ranking entries; kept {len(remainder)} aggregate entries")


if __name__ == "__main__":
    main()
