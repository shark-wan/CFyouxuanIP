from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    path = Path("device-status.json")
    stale = True
    age = "missing"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stamp = datetime.fromisoformat(str(data["updated_at"]).replace("Z", "+00:00"))
            seconds = max(0, (datetime.now(timezone.utc) - stamp).total_seconds())
            age = f"{seconds:.0f}"
            stale = seconds > int(os.environ.get("CF_HEARTBEAT_MAX_AGE", "5400"))
        except (KeyError, TypeError, ValueError, OSError):
            stale = True
    print(f"stale={str(stale).lower()}")
    print(f"age_seconds={age}")


if __name__ == "__main__":
    main()
