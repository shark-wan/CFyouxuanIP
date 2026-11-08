#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${1:-/opt/CFyouxuanIP}"

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This installer expects aarch64 Linux, got $(uname -m)" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 2
fi

mkdir -p "$INSTALL_DIR/bin"
cp "$ROOT/cfyouxuan.py" "$INSTALL_DIR/"
cp "$ROOT/config.json" "$INSTALL_DIR/"
cp -a "$ROOT/aggregate" "$INSTALL_DIR/"
cp -a "$ROOT/engine" "$INSTALL_DIR/"
cp -a "$ROOT/systemd" "$INSTALL_DIR/"

if [[ ! -x "$INSTALL_DIR/bin/cfst" ]]; then
  echo "Downloading CloudflareSpeedTest ARM64..."
  curl -fL "https://github.com/XIU2/CloudflareSpeedTest/releases/download/v2.3.5/cfst_linux_arm64.tar.gz" \
    -o "$INSTALL_DIR/bin/cfst.tar.gz"
  tar -xzf "$INSTALL_DIR/bin/cfst.tar.gz" -C "$INSTALL_DIR/bin"
  rm -f "$INSTALL_DIR/bin/cfst.tar.gz"
  chmod +x "$INSTALL_DIR/bin/cfst"
fi

sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$ROOT/systemd/cfyouxuan.service" \
  > /etc/systemd/system/cfyouxuan.service
cp "$ROOT/systemd/cfyouxuan.timer" /etc/systemd/system/cfyouxuan.timer
systemctl daemon-reload
systemctl enable --now cfyouxuan.timer

echo "Installed to $INSTALL_DIR"
echo "Timer:"
systemctl list-timers cfyouxuan.timer --no-pager