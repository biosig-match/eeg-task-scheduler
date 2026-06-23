from __future__ import annotations

from datetime import datetime, timedelta, timezone

TOKYO_TZ = timezone(timedelta(hours=9), "Asia/Tokyo")


def now_tokyo() -> datetime:
    return datetime.now(TOKYO_TZ)


def now_iso() -> str:
    return now_tokyo().isoformat()
