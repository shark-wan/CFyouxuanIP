#!/usr/bin/env python3
"""Put the locally measured SG ranking at the top of ip.txt."""

import json
import re
from pathlib import Path

RANKING = Path("sg-ranking.json")
IP_FILE = Path("ip.txt")
ENDPOINT_RE = re.compile(r"^(?P<address>\[[^]]+\]|[^:]+):(?P<port>\d+)#")
RANK_PREFIX_RE = re.compile(r"^(?:(?:AAA|BBB|CCC)-)+", re.IGNORECASE)


def canonical_name(name):
    return RANK_PREFIX_RE.sub("", name.strip())


def has_position_labels(line):
    value = line.rsplit("#", 1)[-1].strip()
    return bool(RANK_PREFIX_RE.match(value))


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
    labels = ("AAA", "BBB", "CCC", "", "")
    for slot in slots[:5]:
        if not isinstance(slot, dict):
            continue
        address = str(slot.get("address", "")).strip().strip("[]")
        port = str(slot.get("port", "")).strip()
        name = canonical_name(str(slot.get("source_name") or slot.get("name") or ""))
        if not address or not port.isdigit() or not name:
            continue
        key = (address, port)
        if key in seen:
            continue
        seen.add(key)
        label = labels[len(result)]
        output_name = f"{label}-{name}" if label else name
        result.append(f"{address}:{port}#{output_name}")
    return result


def endpoint(line):
    match = ENDPOINT_RE.match(line.strip())
    return (match.group("address").strip("[]"), match.group("port")) if match else None


def main():
    aggregate = [line.strip() for line in IP_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    ranked = load_ranking()
    ranked_endpoints = {endpoint(line) for line in ranked}
    ranked_endpoints.discard(None)
    # A previous run may have left five ranked lines at the top. Remove that
    # block before applying the new ranking; otherwise displaced entries can
    # leak into the aggregate section. Fresh collection output starts with
    # HK1/HK2/... and is therefore left intact.
    if len(aggregate) >= 3 and all(has_position_labels(line) for line in aggregate[:3]):
        aggregate = aggregate[5:]
    remainder = [
        line
        for line in aggregate
        if endpoint(line) not in ranked_endpoints and not has_position_labels(line)
    ]
    IP_FILE.write_text("\n".join(ranked + remainder) + "\n", encoding="utf-8")
    print(f"applied {len(ranked)} SG ranking entries; kept {len(remainder)} aggregate entries")


if __name__ == "__main__":
    main()
