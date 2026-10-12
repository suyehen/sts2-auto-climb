import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

VERSION = "1.0.0"
_lock = Lock()


def append_record(path: Path, record: dict) -> None:
    """Whitelist callers' report fields; raw bridge/config/token data must not be supplied."""
    payload = dict(record, recorded_at=datetime.now(timezone.utc).isoformat())
    line = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
