#!/usr/bin/env python3
"""CFyouxuanIP: local CloudflareSpeedTest + multi-source aggregate pipeline.

The generated ip.txt is intentionally ordered:
1. locally tested best Cloudflare IPs
2. aggregated external endpoints
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"
DEFAULT_OUTPUT_DIR = ROOT / "output"
CFST_VERSION = "v2.3.5"
CFST_RELEASE = (
    "https://github.com/XIU2/CloudflareSpeedTest/releases/download/"
    f"{CFST_VERSION}/cfst_linux_arm64.tar.gz"
)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def load_config(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as fp:
        config = json.load(fp)
    required = {"cfst", "aggregate", "output"}
    missing = required - config.keys()
    if missing:
        raise ValueError(f"config missing keys: {', '.join(sorted(missing))}")
    return config


def ensure_binary(path: str | pathlib.Path) -> pathlib.Path:
    binary = pathlib.Path(path)
    if not binary.is_absolute():
        binary = ROOT / binary
    if not binary.exists():
        raise FileNotFoundError(
            f"CloudflareSpeedTest binary not found: {binary}. "
            "Run scripts/install.sh or place a compatible binary there."
        )
    if os.name == "posix":
        binary.chmod(0o755)
    return binary


def run_command(
    cmd: list[str],
    cwd: pathlib.Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def run_cfst(config: dict[str, Any], args: argparse.Namespace) -> pathlib.Path:
    cfst_cfg = config["cfst"]
    binary = ensure_binary(cfst_cfg.get("binary", "bin/cfst" if os.name != "nt" else "bin/cfst.exe"))
    ip_file = pathlib.Path(args.ip_file or cfst_cfg.get("ip_file", "ip.txt"))
    if not ip_file.is_absolute():
        ip_file = ROOT / ip_file
    output = pathlib.Path(args.cfst_result or cfst_cfg.get("result_csv", "output/cfst.csv"))
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)

    mode = args.mode or cfst_cfg.get("mode", "httping")
    routines = args.routines or int(cfst_cfg.get("routines", 100))
    ping_count = args.ping_count or int(cfst_cfg.get("ping_times", 4))
    download_count = args.download_count or int(cfst_cfg.get("download_count", 10))
    download_time = args.download_time or int(cfst_cfg.get("download_time", 5))
    max_latency = args.max_latency or int(cfst_cfg.get("max_latency_ms", 300))
    max_loss_rate = args.max_loss_rate or float(cfst_cfg.get("max_loss_rate", 0.2))
    port = args.port or int(cfst_cfg.get("port", 443))
    print_count = args.top or int(config.get("top_n", 20))

    cmd = [
        str(binary),
        "-f", str(ip_file),
        "-o", str(output),
        "-n", str(routines),
        "-t", str(ping_count),
        "-dn", str(download_count),
        "-dt", str(download_time),
        "-tp", str(port),
        "-tl", str(max_latency),
        "-tlr", f"{max_loss_rate:.2f}",
        "-p", str(print_count),
    ]
    if mode == "httping":
        cmd.append("-httping")
        url = args.url or cfst_cfg.get("url")
        if url:
            cmd.extend(["-url", url])
    elif mode != "tcping":
        raise ValueError(f"unsupported mode: {mode}")
    if args.debug:
        cmd.append("-debug")

    print(f"[CFST] {' '.join(cmd)}", flush=True)
    result = run_command(cmd, cwd=ROOT, timeout=float(cfst_cfg.get("timeout_seconds", 1800)))
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        raise RuntimeError(f"CloudflareSpeedTest failed with exit code {result.returncode}")
    if not output.exists():
        raise RuntimeError(f"CloudflareSpeedTest did not produce {output}")
    return output


def parse_cfst_result(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        for raw in reader:
            try:
                ip = (raw.get("IP 地址") or raw.get("IP") or "").strip()
                if not ip:
                    continue
                rows.append(
                    {
                        "ip": ip,
                        "sent": int(raw.get("已发送", 0)),
                        "received": int(raw.get("已接收", 0)),
                        "loss_rate": float(raw.get("丢包率", 0)),
                        "latency_ms": float(raw.get("平均延迟", 0)),
                        "download_mbps": float(raw.get("下载速度(MB/s)", 0)),
                        "colo": (raw.get("地区码") or "CF").strip() or "CF",
                    }
                )
            except (TypeError, ValueError):
                continue

    rows.sort(key=lambda x: (-x["download_mbps"], x["latency_ms"], x["loss_rate"]))
    return rows


def run_aggregate(config: dict[str, Any], args: argparse.Namespace) -> pathlib.Path:
    agg_cfg = config["aggregate"]
    script = ROOT / "aggregate" / "aggregate.py"
    if not script.exists():
        raise FileNotFoundError(f"aggregate script not found: {script}")
    output_dir = pathlib.Path(agg_cfg.get("output_dir", "aggregate/output"))
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.no_aggregate:
        print("[AGG] skipped by --no-aggregate")
    else:
        cmd = [sys.executable, str(script), "--output", str(output_dir)]
        if args.insecure:
            cmd.append("--insecure")
        print(f"[AGG] {' '.join(cmd)}", flush=True)
        result = run_command(cmd, cwd=ROOT, timeout=float(agg_cfg.get("timeout_seconds", 600)))
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode != 0:
            print(
                f"[AGG] warning: aggregate exited with {result.returncode}; "
                "keeping existing output if present",
                file=sys.stderr,
            )

    addresses = output_dir / "addresses.txt"
    if not addresses.exists():
        raise FileNotFoundError(f"aggregate output not found: {addresses}")
    return addresses


def normalize_host(host: str) -> str:
    host = host.strip().strip("[]")
    return host


def parse_aggregate_lines(path: pathlib.Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    endpoint_re = re.compile(r"^(?P<host>.+?):(?P<port>\d+)(?:#(?P<remark>.*))?$")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        match = endpoint_re.match(line)
        if not match:
            continue
        host = normalize_host(match.group("host"))
        port = match.group("port")
        remark = (match.group("remark") or "").strip()
        # Remove source suffix such as [sub.example.com].
        remark = re.sub(r"\s+\[[^]]+\]\s*$", "", remark).strip()
        key = (host.lower(), port)
        if key in seen:
            continue
        seen.add(key)
        items.append({"host": host, "port": port, "remark": remark or "AGG"})
    return items


def short_region(remark: str) -> str:
    """Return a compact region token compatible with ip.txt's host:port#region format."""
    value = re.sub(r"\s+", " ", remark or "").strip()
    match = re.search(r"\b[A-Z]{2}\b", value)
    if match:
        return match.group(0)
    match = re.search(r"\b[A-Z]{3}\b", value)
    if match:
        return match.group(0)
    if any(keyword in value.lower() for keyword in ("域名", "domain")):
        return "DOMAIN"
    return "AGG"


def format_endpoint(host: str, port: str, remark: str) -> str:
    host = normalize_host(host)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}#{remark}"


def write_outputs(
    local_rows: list[dict[str, Any]],
    aggregate_rows: list[dict[str, str]],
    config: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, pathlib.Path]:
    out_cfg = config["output"]
    output_dir = pathlib.Path(args.output_dir or out_cfg.get("dir", "output"))
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    port = str(args.port or config["cfst"].get("port", 443))
    top_n = args.top or int(config.get("top_n", 20))
    local_top = local_rows[:top_n]

    seen: set[tuple[str, str]] = set()
    local_lines: list[str] = []
    for row in local_top:
        key = (normalize_host(row["ip"]).lower(), port)
        if key in seen:
            continue
        seen.add(key)
        local_lines.append(format_endpoint(row["ip"], port, row["colo"] or "CF"))

    aggregate_lines: list[str] = []
    for row in aggregate_rows:
        key = (normalize_host(row["host"]).lower(), row["port"])
        if key in seen:
            continue
        seen.add(key)
        aggregate_lines.append(
            format_endpoint(row["host"], row["port"], short_region(row["remark"]))
        )

    final_lines = local_lines + aggregate_lines
    ip_path = output_dir / "ip.txt"
    local_path = output_dir / "ip.local.txt"
    aggregate_path = output_dir / "ip.aggregate.txt"
    status_path = output_dir / "device-status.json"

    ip_path.write_text("\n".join(final_lines) + "\n", encoding="utf-8")
    local_path.write_text("\n".join(local_lines) + "\n", encoding="utf-8")
    aggregate_path.write_text("\n".join(aggregate_lines) + "\n", encoding="utf-8")

    status = {
        "generated_at": now(),
        "local_count": len(local_lines),
        "aggregate_count": len(aggregate_lines),
        "total_count": len(final_lines),
        "engine": "CloudflareSpeedTest",
        "engine_version": CFST_VERSION,
        "mode": args.mode or config["cfst"].get("mode", "httping"),
    }
    status_path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    repo_root_value = args.repo_root or config.get("git", {}).get("repo_root")
    if repo_root_value:
        repo_root = pathlib.Path(repo_root_value).expanduser().resolve()
        if not repo_root.exists():
            raise FileNotFoundError(f"repo root not found: {repo_root}")
        for name in ("ip.txt", "ip.local.txt", "ip.aggregate.txt", "device-status.json"):
            target = repo_root / name
            shutil.copyfile(output_dir / name, target)
    return {
        "ip": ip_path,
        "local": local_path,
        "aggregate": aggregate_path,
        "status": status_path,
    }

def push_to_github(config: dict[str, Any], args: argparse.Namespace) -> bool:
    git_cfg = config.get("git", {})
    if not (args.push or git_cfg.get("push", False)):
        print("[GIT] push disabled")
        return False
    repo_root = pathlib.Path(args.repo_root or git_cfg.get("repo_root", ROOT)).expanduser().resolve()
    if not (repo_root / ".git").exists():
        raise FileNotFoundError(f"not a git repository: {repo_root}")
    files = ["ip.txt", "ip.local.txt", "ip.aggregate.txt", "device-status.json"]
    add = run_command(["git", "add", "--"] + files, cwd=repo_root)
    if add.returncode != 0:
        raise RuntimeError(add.stderr or "git add failed")
    status = run_command(["git", "status", "--porcelain"], cwd=repo_root)
    if not status.stdout.strip():
        print("[GIT] no changes")
        return False
    message = git_cfg.get(
        "commit_message",
        f"chore: update optimized IP list {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
    )
    commit = run_command(["git", "commit", "-m", message], cwd=repo_root)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr or "git commit failed")
    remote = git_cfg.get("remote", "origin")
    branch = git_cfg.get("branch", "main")
    push = run_command(["git", "push", remote, branch], cwd=repo_root, timeout=300)
    if push.stdout:
        print(push.stdout, end="")
    if push.stderr:
        print(push.stderr, file=sys.stderr, end="")
    if push.returncode != 0:
        raise RuntimeError(f"git push failed with exit code {push.returncode}")
    print("[GIT] pushed successfully")
    return True


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(pathlib.Path(args.config))
    cfst_path = run_cfst(config, args)
    local_rows = parse_cfst_result(cfst_path)
    aggregate_path = run_aggregate(config, args)
    aggregate_rows = parse_aggregate_lines(aggregate_path)
    paths = write_outputs(local_rows, aggregate_rows, config, args)
    print(f"[OUT] {paths['ip'].resolve()}")
    print(
        f"[OUT] local={len(local_rows)} aggregate={len(aggregate_rows)} "
        f"final={len(local_rows[: args.top or config.get('top_n', 20)]) + len(aggregate_rows)}"
    )
    push_to_github(config, args)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(pathlib.Path(args.config))
    checks = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "config": "OK",
        "aggregate_script": (ROOT / "aggregate" / "aggregate.py").exists(),
    }
    try:
        binary = ensure_binary(config["cfst"].get("binary", "bin/cfst" if os.name != "nt" else "bin/cfst.exe"))
        checks["cfst_binary"] = str(binary)
    except Exception as exc:
        checks["cfst_binary"] = f"ERROR: {exc}"
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    if os.name != "posix":
        print("install command is only supported on Linux", file=sys.stderr)
        return 2
    install_dir = pathlib.Path(args.install_dir).resolve()
    install_dir.mkdir(parents=True, exist_ok=True)
    source_dir = ROOT.resolve()
    if source_dir != install_dir:
        for item in (
            "cfyouxuan.py",
            "config.json",
            "aggregate",
            "engine",
            "systemd",
        ):
            src = source_dir / item
            dst = install_dir / item
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

    binary = install_dir / "bin" / "cfst"
    binary.parent.mkdir(parents=True, exist_ok=True)
    if not binary.exists():
        print(f"[INSTALL] downloading CloudflareSpeedTest {CFST_VERSION} ARM64")
        archive = binary.parent / "cfst.tar.gz"
        result = run_command(["curl", "-fL", CFST_RELEASE, "-o", str(archive)], cwd=source_dir, timeout=600)
        if result.returncode != 0:
            raise RuntimeError("failed to download CloudflareSpeedTest")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="cfst-", dir=binary.parent) as temp_dir:
            extract = run_command(["tar", "-xzf", str(archive), "-C", temp_dir], cwd=source_dir, timeout=120)
            if extract.returncode != 0:
                raise RuntimeError("failed to extract CloudflareSpeedTest")
            candidates = sorted(pathlib.Path(temp_dir).rglob("cfst"))
            if not candidates:
                raise RuntimeError("CloudflareSpeedTest binary was not found in the release archive")
            shutil.move(str(candidates[0]), binary)
        archive.unlink(missing_ok=True)
    binary.chmod(0o755)

    service = (ROOT / "systemd" / "cfyouxuan.service").read_text(encoding="utf-8")
    timer = (ROOT / "systemd" / "cfyouxuan.timer").read_text(encoding="utf-8")
    service = service.replace("__INSTALL_DIR__", str(install_dir))
    unit_dir = pathlib.Path("/etc/systemd/system")
    (unit_dir / "cfyouxuan.service").write_text(service, encoding="utf-8")
    (unit_dir / "cfyouxuan.timer").write_text(timer, encoding="utf-8")
    run_command(["systemctl", "daemon-reload"])
    run_command(["systemctl", "enable", "--now", "cfyouxuan.timer"])
    print(f"[INSTALL] installed to {install_dir}")
    print("[INSTALL] systemctl list-timers cfyouxuan.timer")
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run local test, aggregate, write output, optionally push")
    run.add_argument("--ip-file")
    run.add_argument("--mode", choices=("tcping", "httping"))
    run.add_argument("--routines", type=int)
    run.add_argument("--ping-count", type=int)
    run.add_argument("--download-count", type=int)
    run.add_argument("--download-time", type=int)
    run.add_argument("--max-latency", type=int)
    run.add_argument("--max-loss-rate", type=float)
    run.add_argument("--port", type=int)
    run.add_argument("--url")
    run.add_argument("--top", type=int)
    run.add_argument("--cfst-result")
    run.add_argument("--output-dir")
    run.add_argument("--repo-root")
    run.add_argument("--no-aggregate", action="store_true")
    run.add_argument("--insecure", action="store_true")
    run.add_argument("--push", action="store_true")
    run.add_argument("--debug", action="store_true")
    run.set_defaults(func=cmd_run)

    doctor = sub.add_parser("doctor", help="show environment and dependency status")
    doctor.set_defaults(func=cmd_doctor)

    install = sub.add_parser("install", help="install systemd timer on Linux ARM64")
    install.add_argument("--install-dir", default="/opt/CFyouxuanIP")
    install.set_defaults(func=cmd_install)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


