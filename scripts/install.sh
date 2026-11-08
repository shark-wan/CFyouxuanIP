#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${1:-/opt/CFyouxuanIP}"
ROOT_REAL="$(realpath "$ROOT")"
INSTALL_REAL="$(realpath -m "$INSTALL_DIR")"

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This installer expects aarch64 Linux, got $(uname -m)" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 2
fi

mkdir -p "$INSTALL_REAL/bin"

if [[ "$ROOT_REAL" != "$INSTALL_REAL" ]]; then
  tar -C "$ROOT_REAL" -cf - \
    cfyouxuan.py config.json aggregate engine systemd |
    tar -C "$INSTALL_REAL" -xf -
fi

if [[ ! -x "$INSTALL_REAL/bin/cfst" ]]; then
  echo "Downloading CloudflareSpeedTest ARM64..."
  archive="$INSTALL_REAL/bin/cfst.tar.gz"
  curl -fL "https://github.com/XIU2/CloudflareSpeedTest/releases/download/v2.3.5/cfst_linux_arm64.tar.gz" \
    -o "$archive"

  tmpdir="$(mktemp -d "$INSTALL_REAL/bin/cfst.XXXXXX")"
  trap 'rm -rf "$tmpdir"' EXIT
  tar -xzf "$archive" -C "$tmpdir"
  cfst_bin="$(find "$tmpdir" -type f -name cfst -print -quit)"
  if [[ -z "$cfst_bin" ]]; then
    echo "CloudflareSpeedTest binary not found in release archive" >&2
    exit 2
  fi
  install -m 0755 "$cfst_bin" "$INSTALL_REAL/bin/cfst"
  rm -f "$archive"
fi

sed "s|__INSTALL_DIR__|$INSTALL_REAL|g" "$ROOT_REAL/systemd/cfyouxuan.service" \
  > /etc/systemd/system/cfyouxuan.service
cp "$ROOT_REAL/systemd/cfyouxuan.timer" /etc/systemd/system/cfyouxuan.timer
systemctl daemon-reload
systemctl enable --now cfyouxuan.timer

echo "Installed to $INSTALL_REAL"
echo "Timer:"
systemctl list-timers cfyouxuan.timer --no-pager