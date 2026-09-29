"""Reporting on events that didn't qualify: the `rejected` CLI and the summary printed after each run."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, UTC

from nymetro_eventlocator.db.store import Store

REASON_LABELS = {
    "excluded_keyword": "excluded keyword",
    "no_category": "no matching category",
    "out_of_region": "outside covered areas",
    "online_only": "online only",
    "duplicate": "duplicate",
    "route_filter": "no route matched",
    "parse_error": "unparseable",
    "cancelled": "cancelled",
    "manual_exclude": "excluded in overrides.yaml",
}


def parse_since(value: str | None) -> datetime | None:
    """'7d', '12h', '30m' -> a UTC datetime that far back; None -> no limit."""
    if not value:
        return None
    m = re.fullmatch(r"(\d+)\s*([dhm])", value.strip().lower())
    if not m:
        raise ValueError(f"--since expects something like 7d, 12h or 30m, got {value!r}")
    n, unit = int(m[1]), m[2]
    delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
    return datetime.now(UTC) - delta


def summarize(store: Store, since: datetime | None = None) -> tuple[int, Counter, dict[str, Counter]]:
    """Total, count per reason, and per-reason breakdown by detail for reasons where the detail is informative."""
    rows = store.rejections(since=since)
    by_reason = Counter(r["reason"] for r in rows)
    details: dict[str, Counter] = {}
    for r in rows:
        if r["reason"] == "excluded_keyword":
            details.setdefault(r["reason"], Counter())[r["detail"]] += 1
        elif r["reason"] in ("no_category", "online_only", "out_of_region", "duplicate"):
            details.setdefault(r["reason"], Counter())[r["source"]] += 1
    return len(rows), by_reason, details


def format_summary(store: Store, since: datetime | None = None) -> str:
    """e.g. 'Rejected 143: no_category 88 (ical 60, ticketmaster 28), excluded_keyword 31 (casino 22, toddler 9), ...'"""
    total, by_reason, details = summarize(store, since)
    if not total:
        return "Rejected 0"
    parts = []
    for reason, n in by_reason.most_common():
        sub = details.get(reason)
        extra = f" ({', '.join(f'{k} {v}' for k, v in sub.most_common(4))})" if sub else ""
        parts.append(f"{reason} {n}{extra}")
    return f"Rejected {total}: " + ", ".join(parts)


def format_rows(rows, tz, limit: int | None = None) -> list[str]:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(tz)
    out = []
    for r in rows[:limit] if limit else rows:
        when = datetime.fromisoformat(r["start"]).astimezone(zone).strftime("%a %b %d %I:%M%p") if r["start"] else "?"
        detail = f" ({r['detail']})" if r["detail"] else ""
        out.append(f"{r['reason']}{detail} | {r['source']} | {when} | {r['title']} | {r['url']}")
    return out
