#!/usr/bin/env python3
import base64
import re
import ssl
import sys
import urllib.parse
import urllib.request

SOURCES = (
    "sub.cmliussss.net",
    "owo.o00o.ooo",
    "cm.soso.edu.kg",
    "zrf.zrf.me",
    "sub.keaeye.icu",
    "sub.mot.cloudns.biz",
    "sub.mia.xx.kg",
    "sub.lzjbaby.com",
    "sub.xdu.qzz.io",
)

REGION_ORDER = ("HK", "SG", "MY", "JP")
REGION_WORDS = {
    "HK": ("HK", "HONG KONG", "\u9999\u6e2f", "\U0001f1ed\U0001f1f0"),
    "SG": ("SG", "SINGAPORE", "\u65b0\u52a0\u5761", "\U0001f1f8\U0001f1ec"),
    "MY": ("MY", "MALAYSIA", "\u9a6c\u6765", "\U0001f1f2\U0001f1fe"),
    "JP": ("JP", "JAPAN", "\u65e5\u672c", "\U0001f1ef\U0001f1f5"),
}

UA = "v2rayN/edgetunnel (https://github.com/cmliu/edgetunnel)"
SUB_PATH = "/sub?host=example.com&uuid=00000000-0000-4000-8000-000000000000"
URI_RE = re.compile(
    r"^[a-z][a-z0-9+.-]*://[^@\n]+@(?P<addr>\[[^\]]+\]|[^:/?#]+):(?P<port>\d+)[^#\n]*(?:#(?P<name>[^\r\n]*))?",
    re.I,
)
def read_url(host):
    req = urllib.request.Request(f"https://{host}{SUB_PATH}", headers={"User-Agent": UA})
    # The source list is fixed by the repo owner; several sources use incomplete cert chains.
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=20, context=context) as response:
        return response.read().decode("utf-8", "replace")


def maybe_b64decode(text):
    clean = re.sub(r"\s+", "", text)
    if not clean or len(clean) % 4 or not re.fullmatch(r"[A-Za-z0-9+/_-]+={0,2}", clean):
        return text
    padded = clean + "=" * (-len(clean) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decoder(padded).decode("utf-8", "replace")
        except Exception:
            pass
    return text


def detect_region(name):
    upper = name.upper()
    for region in REGION_ORDER:
        for word in REGION_WORDS[region]:
            if word.isascii():
                if re.search(rf"(?<![A-Z0-9]){re.escape(word)}(?![A-Z0-9])", upper):
                    return region
            elif word in name:
                return region
    return None


def parse_items(text):
    for line in maybe_b64decode(text).splitlines():
        match = URI_RE.search(line.strip())
        if not match:
            continue
        name = urllib.parse.unquote(match.group("name") or "")
        region = detect_region(name)
        if not region:
            continue
        address = match.group("addr").strip("[]")
        port = match.group("port")
        yield region, address, port


def collect():
    seen = set()
    grouped = {region: [] for region in REGION_ORDER}
    errors = []

    for source in SOURCES:
        try:
            for region, address, port in parse_items(read_url(source)):
                key = (address, port)
                if key in seen:
                    continue
                seen.add(key)
                grouped[region].append((address, port))
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}: {exc}")

    lines = []
    for region in REGION_ORDER:
        for index, (address, port) in enumerate(grouped[region], 1):
            lines.append(f"{address}:{port}#{region}{index}")

    if not lines:
        raise RuntimeError("no matching HK/SG/MY/JP nodes collected; " + "; ".join(errors))

    if errors:
        print("source warnings:", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
    return "\n".join(lines) + "\n"


def main():
    Path("ip.txt").write_text(collect(), encoding="utf-8")


if __name__ == "__main__":
    main()
