#!/usr/bin/env python3
"""Aggregate edgetunnel BEST_SUB endpoints from multiple subscription sources."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import dataclasses
import datetime as dt
import ipaddress
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_SOURCES = [
    "sub.cmliussss.net",
    "owo.o00o.ooo",
    "cm.soso.edu.kg",
    "zrf.zrf.me",
    "sub.keaeye.icu",
    "sub.mot.cloudns.biz",
    "sub.mia.xx.kg",
    "sub.lzjbaby.com",
    "sub.xdu.qzz.io",
]
DEFAULT_PORT = 443
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
USER_AGENT = "v2rayN/edgetunnel (https://github.com/cmliu/edgetunnel)"
BEST_SUB_QUERY = {
    "host": "example.com",
    "uuid": "00000000-0000-4000-8000-000000000000",
}
BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
DOMAIN_LABEL_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
ENDPOINT_RE = re.compile(
    r"^(\[[0-9A-Fa-f:]+\]|[0-9.]+|[a-zA-Z0-9._~!$&'()*+,;=-]+)"
    r"(?::(\d+))?$"
)


@dataclasses.dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    kind: str
    remark: str = ""
    scheme: str = ""

    @property
    def key(self) -> tuple[str, int]:
        return canonical_host(self.host), self.port

    @property
    def display_host(self) -> str:
        return format_host(self.host)


@dataclasses.dataclass
class SourceResult:
    source: str
    label: str
    url: str
    ok: bool
    endpoints: list[Endpoint] = dataclasses.field(default_factory=list)
    error: str = ""
    http_status: int | None = None
    response_bytes: int = 0
    content_type: str = ""


def canonical_host(host: str) -> str:
    host = host.strip().strip("[]")
    try:
        return str(ipaddress.ip_address(host)).lower()
    except ValueError:
        return host.rstrip(".").lower()


def format_host(host: str) -> str:
    host = host.strip()
    if ":" in host and not host.startswith("["):
        try:
            ipaddress.IPv6Address(host)
            return f"[{host}]"
        except ValueError:
            pass
    return host


def endpoint_kind(host: str) -> str | None:
    value = host.strip().strip("[]")
    try:
        address = ipaddress.ip_address(value)
        return "ipv6" if address.version == 6 else "ipv4"
    except ValueError:
        if is_domain(value):
            return "domain"
    return None


def is_domain(value: str) -> bool:
    if not value or len(value) > 253 or value.endswith("."):
        return False
    labels = value.rstrip(".").split(".")
    return len(labels) >= 2 and all(DOMAIN_LABEL_RE.match(label) for label in labels)


def normalize_source(source: str) -> tuple[str, str]:
    """Return (request URL, default source label) for a configured source."""
    raw = source.strip()
    if not raw:
        raise ValueError("empty source")

    label_part = ""
    if "#" in raw:
        raw, label_part = raw.split("#", 1)

    value = raw
    if value.lower().startswith("sub://"):
        value = value[len("sub://"):]
    parsed = urllib.parse.urlsplit(value)

    if parsed.scheme in ("http", "https") and parsed.netloc:
        label = label_part or parsed.hostname or parsed.netloc
        if parsed.path in ("", "/sub"):
            query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            existing_keys = {key for key, _ in query_items}
            query_items.extend(
                (key, value) for key, value in BEST_SUB_QUERY.items() if key not in existing_keys
            )
            query = urllib.parse.urlencode(query_items)
            url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/sub", query, ""))
            return url, label
        return value, label

    # A bare hostname (including sub://host), optionally followed by ?query.
    host_part = value
    query = parsed.query
    if "?" in host_part:
        host_part = host_part.split("?", 1)[0]
    if not host_part or "/" in host_part:
        raise ValueError(f"unsupported source: {source!r}")

    query_items = urllib.parse.parse_qsl(query, keep_blank_values=True)
    query_items.extend(BEST_SUB_QUERY.items())
    url = urllib.parse.urlunsplit(("https", host_part, "/sub", urllib.parse.urlencode(query_items), ""))
    label = label_part or host_part.split("@", 1)[-1].split(":", 1)[0]
    return url, label


def decode_body(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    compact = re.sub(r"\s+", "", text)
    if compact and len(compact) % 4 == 0 and BASE64_RE.fullmatch(compact):
        try:
            decoded = base64.b64decode(compact, validate=True).decode("utf-8", errors="replace")
            # Avoid treating ordinary text that happens to be valid base64 as a subscription.
            if "://" in decoded or ":" in decoded:
                return decoded
        except (ValueError, UnicodeDecodeError):
            pass
    return text


def fetch_source(
    source: str,
    *,
    timeout: float,
    retries: int = 1,
    insecure: bool = False,
) -> SourceResult:
    try:
        url, label = normalize_source(source)
    except ValueError as exc:
        return SourceResult(source, source, source, False, error=str(exc))

    result = SourceResult(source, label, url, False)
    context = None
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
                result.http_status = response.status
                result.response_bytes = len(body)
                result.content_type = response.headers.get("Content-Type", "")
                text = decode_body(body)
            result.endpoints = parse_response(text)
            result.ok = True
            return result
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            result.http_status = getattr(exc, "code", result.http_status)
            if attempt + 1 < max(1, retries):
                continue
    result.error = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error"
    return result


def parse_response(text: str, *, parse_all_proxy_links: bool = False) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
    if not lines:
        return endpoints

    if looks_like_ip_csv(lines):
        endpoints.extend(parse_csv(lines))
        return endpoints

    for line in lines:
        endpoint = parse_proxy_link(line, parse_all=parse_all_proxy_links)
        if endpoint is None:
            endpoint = parse_endpoint_line(line)
        if endpoint is not None:
            endpoints.append(endpoint)
    return endpoints


def looks_like_ip_csv(lines: Sequence[str]) -> bool:
    if len(lines) < 2 or "," not in lines[0]:
        return False
    headers = [item.strip().lower() for item in lines[0].split(",")]
    return (
        ("ip地址" in headers and "端口" in headers)
        or (any("ip" in item for item in headers) and any("延迟" in item for item in headers))
    )


def parse_csv(lines: Sequence[str]) -> list[Endpoint]:
    headers = [item.strip() for item in lines[0].split(",")]
    lower = [item.lower() for item in headers]

    def index_of(*names: str) -> int | None:
        for name in names:
            if name in lower:
                return lower.index(name)
            matches = [i for i, item in enumerate(lower) if name in item]
            if matches:
                return matches[0]
        return None

    ip_i = index_of("ip地址", "ip")
    port_i = index_of("端口")
    if ip_i is None:
        return []
    remark_i = index_of("国家", "城市", "数据中心")
    tls_i = next((i for i, item in enumerate(lower) if item == "tls"), None)

    endpoints: list[Endpoint] = []
    for line in lines[1:]:
        values = [item.strip() for item in line.split(",")]
        if len(values) <= ip_i:
            continue
        if tls_i is not None and tls_i < len(values) and values[tls_i].lower() not in ("true", "yes", "1"):
            continue
        host = values[ip_i]
        kind = endpoint_kind(host)
        if kind is None:
            continue
        port = int(values[port_i]) if port_i is not None and port_i < len(values) and values[port_i].isdigit() else DEFAULT_PORT
        remark = values[remark_i] if remark_i is not None and remark_i < len(values) else ""
        endpoints.append(Endpoint(host, port, kind, remark))
    return endpoints


def parse_proxy_link(link: str, *, parse_all: bool = False) -> Endpoint | None:
    marker = "00000000-0000-4000-8000-000000000000" in link and "example.com" in link
    if not marker and not parse_all:
        return None

    scheme, rest = urllib.parse.urlsplit(link).scheme, link
    if not scheme:
        return None

    if scheme.lower() == "vmess":
        endpoint = parse_vmess_link(link)
        if endpoint is not None:
            return endpoint

    try:
        parsed = urllib.parse.urlsplit(link)
        if not parsed.hostname:
            return None
        port = parsed.port
    except (ValueError, urllib.error.URLError):
        return None
    if port is None:
        return None

    kind = endpoint_kind(parsed.hostname)
    if kind is None:
        return None
    remark = urllib.parse.unquote(parsed.fragment) if parsed.fragment else ""
    return Endpoint(parsed.hostname, port, kind, remark, scheme.lower())


def parse_vmess_link(link: str) -> Endpoint | None:
    payload = link[len("vmess://") :].split("#", 1)[0]
    try:
        obj = json.loads(base64.b64decode(payload, validate=True).decode("utf-8"))
        host = str(obj.get("add", "")).strip()
        port = int(obj["port"])
        remark = str(obj.get("ps", ""))
    except (KeyError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    kind = endpoint_kind(host)
    if kind is None:
        return None
    return Endpoint(host, port, kind, remark, "vmess")


def parse_endpoint_line(line: str) -> Endpoint | None:
    raw = line.strip()
    if "://" in raw.split("#", 1)[0]:
        return None

    host_text = raw.split("#", 1)[0].strip()
    remark = urllib.parse.unquote(raw.split("#", 1)[1].strip()) if "#" in raw else ""
    match = ENDPOINT_RE.fullmatch(host_text)
    if not match:
        return None

    host = match.group(1)
    port = int(match.group(2)) if match.group(2) else DEFAULT_PORT
    if not 1 <= port <= 65535:
        return None
    kind = endpoint_kind(host)
    if kind is None:
        return None
    return Endpoint(host, port, kind, remark)


def aggregate(
    sources: Iterable[str],
    *,
    timeout: float,
    retries: int,
    workers: int,
    insecure: bool,
    parse_all_proxy_links: bool,
) -> tuple[list[SourceResult], dict[tuple[str, int], dict[str, object]]]:
    source_list = list(sources)
    # Request concurrently, but retain configured source order for deterministic output.
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                fetch_source,
                source,
                timeout=timeout,
                retries=retries,
                insecure=insecure,
            ): source
            for source in source_list
        }
        by_source = {source: None for source in source_list}  # type: ignore[dict-as-any]
        for future in concurrent.futures.as_completed(futures):
            result: SourceResult = future.result()
            by_source[result.source] = result

    aggregated: dict[tuple[str, int], dict[str, object]] = {}
    for source in source_list:
        result: SourceResult | None = by_source[source]
        if result is None:
            continue
        for endpoint in result.endpoints:
            item = aggregated.get(endpoint.key)
            if item is None:
                item = {
                    "host": endpoint.host,
                    "port": endpoint.port,
                    "type": endpoint.kind,
                    "remark": endpoint.remark,
                    "schemes": [],
                    "sources": [],
                }
                aggregated[endpoint.key] = item
            if result.label not in item["sources"]:  # type: ignore[operator]
                item["sources"].append(result.label)  # type: ignore[union-attr]
            if endpoint.scheme and endpoint.scheme not in item["schemes"]:  # type: ignore[operator]
                item["schemes"].append(endpoint.scheme)  # type: ignore[union-attr]

    return [result for result in by_source.values() if result is not None], aggregated


def endpoint_text(item: dict[str, object], *, include_sources: bool) -> str:
    host = str(item["host"])
    port = int(item["port"])  # type: ignore[arg-type]
    text = f"{format_host(host)}:{port}"
    remark = str(item.get("remark", ""))
    source_list = item.get("sources", [])
    if include_sources and source_list:
        labels = "|".join(str(label) for label in source_list)
        remark = f"{remark} [{labels}]".strip()
    if remark:
        text += f"#{remark}"
    return text


def write_outputs(
    output_dir: Path,
    results: Sequence[SourceResult],
    aggregated: dict[tuple[str, int], dict[str, object]],
    *,
    include_sources: bool,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    endpoints = list(aggregated.values())
    lines = [endpoint_text(item, include_sources=include_sources) for item in endpoints]

    paths = {
        "addresses": output_dir / "addresses.txt",
        "ipv4": output_dir / "ipv4.txt",
        "ipv6": output_dir / "ipv6.txt",
        "domains": output_dir / "domains.txt",
        "json": output_dir / "results.json",
    }
    paths["addresses"].write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
    for kind, path in (("ipv4", paths["ipv4"]), ("ipv6", paths["ipv6"]), ("domain", paths["domains"])):
        selected = [line for item, line in zip(endpoints, lines) if item["type"] == kind]
        path.write_text("\n".join(selected) + ("\n" if selected else ""), encoding="utf-8", newline="\n")

    counts = {kind: sum(1 for item in endpoints if item["type"] == kind) for kind in ("ipv4", "ipv6", "domain")}
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "total": len(endpoints),
        "counts": counts,
        "sources": [
            {
                "source": result.source,
                "label": result.label,
                "url": result.url,
                "ok": result.ok,
                "endpoint_count": len(result.endpoints),
                "http_status": result.http_status,
                "response_bytes": result.response_bytes,
                "content_type": result.content_type,
                "error": result.error,
            }
            for result in results
        ],
        "endpoints": endpoints,
    }
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return paths


def load_config(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate edgetunnel BEST_SUB IP/domain endpoints")
    parser.add_argument("--config", type=Path, help="JSON config (default: config.json beside this script)")
    parser.add_argument("--source", action="append", dest="sources", help="Source host/URL; repeatable. Overrides config")
    parser.add_argument("--output", type=Path, help="Output directory (overrides config)")
    parser.add_argument("--timeout", type=float, help="Per-request timeout in seconds")
    parser.add_argument("--retries", type=int, help="Total attempts per source (>=1)")
    parser.add_argument("--workers", type=int, help="Concurrent fetch workers")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification")
    parser.add_argument("--parse-all-proxy-links", action="store_true", help="Also parse non-BEST_SUB proxy node links")
    parser.add_argument("--no-source-remarks", action="store_true", help="Do not append source labels to remarks")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    parser = build_parser()
    args = parser.parse_args(argv)

    config_path = args.config or (script_dir / "config.json")
    config = load_config(config_path) if config_path.exists() else {}

    sources = args.sources or config.get("sources") or DEFAULT_SOURCES
    if not isinstance(sources, list) or not sources:
        parser.error("no sources configured")

    output_value = args.output or config.get("output_dir", "output")
    output_dir = Path(output_value)
    if not args.output and not Path(output_value).is_absolute():
        output_dir = script_dir / output_dir

    settings = {
        "timeout": args.timeout if args.timeout is not None else float(config.get("timeout", 20)),
        "retries": args.retries if args.retries is not None else int(config.get("retries", 2)),
        "workers": args.workers if args.workers is not None else int(config.get("workers", 9)),
        "insecure": args.insecure or bool(config.get("insecure", False)),
        "parse_all_proxy_links": args.parse_all_proxy_links or bool(config.get("parse_all_proxy_links", False)),
    }
    if settings["timeout"] <= 0 or settings["retries"] < 1 or settings["workers"] < 1:
        parser.error("timeout/workers must be positive and retries must be >= 1")

    results, aggregated = aggregate(sources, **settings)  # type: ignore[arg-type]
    include_sources = not args.no_source_remarks and bool(config.get("include_source_remarks", True))
    paths = write_outputs(
        output_dir,
        results,
        aggregated,
        include_sources=include_sources,
    )

    successful = sum(result.ok for result in results)
    counts = {kind: sum(1 for item in aggregated.values() if item["type"] == kind) for kind in ("ipv4", "ipv6", "domain")}
    print(f"Sources: {successful}/{len(results)} successful")
    print(f"Endpoints: {len(aggregated)} (IPv4 {counts['ipv4']}, IPv6 {counts['ipv6']}, domain {counts['domain']})")
    print(f"Output: {output_dir.resolve()}")
    for result in results:
        status = "OK" if result.ok else "FAIL"
        detail = f"{len(result.endpoints)} endpoints" if result.ok else result.error
        print(f"  [{status}] {result.label}: {detail}")

    failed = [result for result in results if not result.ok]
    print(f"Details: {paths['json'].resolve()}")
    return 0 if not failed else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)


