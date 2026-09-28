"""GitHub runner fallback used when the Android heartbeat is stale."""

from __future__ import annotations

import concurrent.futures
import os
import socket
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sg_optimizer import speed_probe, fetch_subscription, parse_candidates  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AGGREGATE = ROOT / "ip.aggregate.txt"
OUTPUT = ROOT / "ip.txt"
ATTEMPTS = 2
TIMEOUT = 3.0


def parse_aggregate() -> list[tuple[str, int, str]]:
    rows = []
    for raw in AGGREGATE.read_text(encoding="utf-8").splitlines():
        if "#" not in raw or ":" not in raw.split("#", 1)[0]:
            continue
        endpoint, name = raw.strip().split("#", 1)
        host, port_text = endpoint.rsplit(":", 1)
        try:
            rows.append((host.strip("[]"), int(port_text), name.strip()))
        except ValueError:
            pass
    return rows


def probe(row: tuple[str, int, str]) -> tuple[str, int, str, float] | None:
    host, port, name = row
    samples = []
    for _ in range(ATTEMPTS):
        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), TIMEOUT):
                samples.append((time.perf_counter() - started) * 1000)
        except OSError:
            pass
    if not samples:
        return None
    return host, port, name, round(statistics.median(samples), 2)


def main() -> None:
    # A fallback runner must never replace a valid ranked file with an
    # unranked TCP list.  The subscription is intentionally kept out of the
    # repository, so an unset or unusable secret is a normal configuration
    # state.  In that case leave the last Android result intact and let the
    # next heartbeat restore normal operation.
    if not os.environ.get("SG_SUB_URL", "").strip():
        print("fallback skipped: SG_SUB_URL/CF_SUB_URL secret is not configured")
        return

    rows = parse_aggregate()
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(64, max(1, len(rows)))) as pool:
        reachable = [item for item in pool.map(probe, rows) if item]
    reachable.sort(key=lambda item: item[3])

    try:
        subscription = fetch_subscription()
        candidates = parse_candidates(subscription, "SG") + parse_candidates(subscription, "JP")
    except Exception as exc:
        print(f"fallback skipped: subscription unavailable ({type(exc).__name__})")
        return
    by_endpoint = {(node["address"], node["port"]): node for node in candidates}
    if not by_endpoint:
        print("fallback skipped: subscription contained no SG/JP candidates")
        return
    top = [item for item in reachable if (item[0], item[1]) in by_endpoint][:10]
    measured = []
    for host, port, name, tcp_ms in top:
        node = dict(by_endpoint[(host, port)])
        node["tcp_ms"] = tcp_ms
        result = speed_probe(node)
        if result.get("speed_bps"):
            measured.append((host, port, name, tcp_ms, result["speed_bps"]))
    measured.sort(key=lambda item: (-item[4], item[3]))
    ranked = measured[:5]
    if len(ranked) < 5:
        print(f"fallback skipped: only {len(ranked)} SG/JP speed tests completed; keeping last ranked file")
        return
    ranked_keys = {(host, port) for host, port, *_ in ranked}

    labels = ("AAA", "BBB", "CCC", "DDD", "EEE")
    output = [
        f"{host}:{port}#{label}-{name}"
        for label, (host, port, name, _tcp, _speed) in zip(labels, ranked)
    ]
    for host, port, name, _tcp in reachable:
        if (host, port) not in ranked_keys:
            output.append(f"{host}:{port}#{name}")
    OUTPUT.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(f"fallback wrote {len(output)} reachable nodes and {len(ranked)} speed-ranked nodes")


if __name__ == "__main__":
    main()
