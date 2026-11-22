from datetime import datetime, timedelta, timezone
import re
from dateutil import parser


def parse_timestamp(value):
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    relative = re.fullmatch(
        r"(?i)(\d+)\s+(second|minute|hour|day)s?\s+ago",
        value,
    )
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        delta = {
            "second": timedelta(seconds=amount),
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
        }[unit]
        return datetime.now(timezone.utc) - delta
    try:
        parsed = parser.parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def within_24h(value, now=None):
    parsed = parse_timestamp(value)
    if not parsed:
        return False
    now = now or datetime.now(timezone.utc)
    return now - timedelta(hours=24) <= parsed <= now + timedelta(minutes=5)
