from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    path = Path("device-status.json")
    stale = True
    age = "missing"
    degraded = False
    abandon = False

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("status is not an object")
            if data.get("publish", {}).get("ip_ok") is False:
                raise ValueError("last ip.txt publication failed")
            stamp = datetime.fromisoformat(str(data["updated_at"]).replace("Z", "+00:00"))
            seconds = max(0, (datetime.now(timezone.utc) - stamp).total_seconds())
            age = f"{seconds:.0f}"

            # 阈值配置
            max_age = int(os.environ.get("CF_HEARTBEAT_MAX_AGE", "5400"))
            degrade_threshold = 3660  # 61 分钟
            abandon_threshold = 21600  # 6 小时

            stale = seconds > max_age
            degraded = seconds > degrade_threshold
            abandon = seconds > abandon_threshold

        except (KeyError, TypeError, ValueError, OSError):
            stale = True
            degraded = True
            abandon = False  # 文件损坏不等于长期失联

    print(f"stale={str(stale).lower()}")
    print(f"degraded={str(degraded).lower()}")
    print(f"abandon={str(abandon).lower()}")
    print(f"age_seconds={age}")


if __name__ == "__main__":
    main()
