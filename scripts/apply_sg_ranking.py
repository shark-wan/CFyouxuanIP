#!/usr/bin/env python3
"""Put the locally measured SG and JP rankings at the top of ip.txt."""

import json
import re
from pathlib import Path

RANKING = Path("sg-ranking.json")
IP_FILE = Path("ip.txt")
ENDPOINT_RE = re.compile(r"^(?P<address>\[[^]]+\]|[^:]+):(?P<port>\d+)#")
RANK_PREFIX_RE = re.compile(r"^(?:(?:AAA|BBB|CCC)-)+", re.IGNORECASE)
REGION_NAME_RE = {
    "SG": re.compile(r"^SG(?:$|[-_]?\d)", re.IGNORECASE),
    "JP": re.compile(r"^JP(?:$|[-_]?\d)", re.IGNORECASE),
}


def canonical_name(name):
    return RANK_PREFIX_RE.sub("", name.strip())


def has_position_labels(line):
    return bool(RANK_PREFIX_RE.match(line_name(line)))


def line_name(line):
    return line.rsplit("#", 1)[-1].strip()


def load_group(slots, labels, seen):
    result = []
    for slot in slots[:len(labels)]:
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


def load_ranking():
    if not RANKING.exists():
        return []
    try:
        data = json.loads(RANKING.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    sg = data.get("slots", [])
    jp = data.get("jp_slots", [])
    seen = set()
    return load_group(sg, ("AAA", "BBB", "CCC", "", ""), seen) + load_group(
        jp, ("AAA", "BBB", ""), seen
    )


def is_region_line(line, region, prefixed=None):
    name = line_name(line)
    if prefixed is None:
        prefixed = bool(RANK_PREFIX_RE.match(name))
    if prefixed and not RANK_PREFIX_RE.match(name):
        return False
    return bool(REGION_NAME_RE[region].match(canonical_name(name)))


def old_rank_block_length(lines):
    """Recognize the block written by the previous version of this script."""
    if len(lines) < 3 or not all(is_region_line(line, "SG", prefixed=True) for line in lines[:3]):
        return 0
    length = 3
    while length < 5 and length < len(lines) and is_region_line(lines[length], "SG"):
        length += 1
    if length == 5 and len(lines) >= 8 and all(
        is_region_line(lines[index], "JP", prefixed=True) for index in (5, 6)
    ) and is_region_line(lines[7], "JP", prefixed=False):
        return 8
    return length


def endpoint(line):
    match = ENDPOINT_RE.match(line.strip())
    return (match.group("address").strip("[]"), match.group("port")) if match else None


def main():
    aggregate = [line.strip() for line in IP_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    ranked = load_ranking()
    ranked_endpoints = {endpoint(line) for line in ranked}
    ranked_endpoints.discard(None)
    block_length = old_rank_block_length(aggregate)
    if block_length:
        aggregate = aggregate[block_length:]
    remainder = [
        line
        for line in aggregate
        if endpoint(line) not in ranked_endpoints and not has_position_labels(line)
    ]
    IP_FILE.write_text("\n".join(ranked + remainder) + "\n", encoding="utf-8")
    print(f"applied {len(ranked)} SG/JP ranking entries; kept {len(remainder)} aggregate entries")


if __name__ == "__main__":
    main()
