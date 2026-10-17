#!/usr/bin/env python3
"""Validate repository node output before a workflow commits it."""

from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path

ENDPOINT_RE = re.compile(r"^(?P<host>\[[^\]]+\]|[^:#\s]+):(?P<port>\d+)#(?P<name>[^\s#]+)$")
REGIONS = {"HK", "JP", "KR", "SG", "MY"}
RANK_LABELS = ("AAA", "BBB", "CCC", "DDD", "EEE")


def parse_endpoint(line: str) -> tuple[str, int, str] | None:
    match = ENDPOINT_RE.fullmatch(line.strip())
    if not match:
        return None
    host = match.group("host").strip("[]")
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or host.startswith("."):
            return None
    return host, port, match.group("name")


def validate_aggregate(path: Path) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, int]] = set()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if not lines:
        return ["aggregate is missing or empty"]
    for number, line in enumerate(lines, 1):
        parsed = parse_endpoint(line)
        if not parsed:
            errors.append(f"aggregate line {number} has invalid endpoint format")
            continue
        host, port, name = parsed
        key = (host.lower(), port)
        if key in seen:
            errors.append(f"aggregate line {number} duplicates an endpoint")
        seen.add(key)
        region = re.match(r"^(HK|JP|KR|SG|MY)(?:[-_]?\d+)?$", name.split("-", 1)[0].split("_", 1)[0])
        if not region:
            errors.append(f"aggregate line {number} has unsupported region label")
    return errors


def validate_ip(path: Path) -> list[str]:
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    # Abandon mode: pure aggregate dump (no ranking at all)
    if lines and not any(line.split("#", 1)[-1].startswith(("AAA", "BBB", "CCC", "DDD", "EEE", "旧-")) for line in lines if "#" in line):
        # Validate as aggregate format
        for number, line in enumerate(lines, 1):
            parsed = parse_endpoint(line)
            if not parsed:
                errors.append(f"ip.txt line {number} has invalid endpoint format (abandon mode)")
            else:
                host, port, name = parsed
                region = re.match(r"^(HK|JP|KR|SG|MY)(?:[-_]?\d+)?$", name.split("-", 1)[0].split("_", 1)[0])
                if not region:
                    errors.append(f"ip.txt line {number} has unsupported region label (abandon mode)")
        return errors

    if len(lines) < len(RANK_LABELS):
        return ["ip.txt has fewer than five ranked entries"]

    seen: set[tuple[str, int]] = set()
    for number, line in enumerate(lines, 1):
        parsed = parse_endpoint(line)
        if not parsed:
            errors.append(f"ip.txt line {number} has invalid endpoint format")
            continue
        host, port, name = parsed
        key = (host.lower(), port)
        if key in seen:
            errors.append(f"ip.txt line {number} duplicates an endpoint")
        seen.add(key)

        # Accept both "AAA-SG65" and "旧-AAA-SG65"
        stripped = name[2:] if name.startswith("旧-") else name
        if number <= len(RANK_LABELS):
            expected = RANK_LABELS[number - 1] + "-"
            if not stripped.startswith(expected):
                errors.append(f"ip.txt line {number} is missing {RANK_LABELS[number - 1]} label")
        elif not re.match(r"^\d+\.", stripped):
            errors.append(f"ip.txt line {number} is missing secondary rank")
    return errors


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("ip.aggregate.txt")
    if not target.exists():
        print(f"missing output: {target}", file=sys.stderr)
        return 1
    errors = validate_aggregate(target) if target.name == "ip.aggregate.txt" else validate_ip(target)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"validated {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
