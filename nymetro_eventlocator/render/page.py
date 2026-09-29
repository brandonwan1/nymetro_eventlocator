"""Render output/index.html: every stored upcoming event plus the rejections, with client-side filters and a map.

All event text comes from third parties, so the page only ever inserts it with textContent (never innerHTML),
and only http(s) links are rendered.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, UTC
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from nymetro_eventlocator.config import Config
from nymetro_eventlocator.db.store import Store
from nymetro_eventlocator.models import Event
from nymetro_eventlocator.rejections import REASON_LABELS, format_summary
from nymetro_eventlocator.timefmt import fmt_day, fmt_day_time, fmt_time

TEMPLATES = Path(__file__).parent / "templates"

# OpenFreeMap: free vector tiles, no key/registration/usage limits (openfreemap.org, checked 2026-09-23).
# OpenStreetMap's own tile server is not used: its policy requires a Referer, which a file:// page can't send.
DEFAULT_MAP = {
    "style_light": "https://tiles.openfreemap.org/styles/positron",
    "style_dark": "https://tiles.openfreemap.org/styles/dark",
    "js": "https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/maplibre-gl.js",
    "css": "https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/maplibre-gl.css",
}


SOURCE_LABELS = {"meetup.com": "Meetup", "lu.ma": "Luma", "luma.com": "Luma", "ticketmaster.com": "Ticketmaster",
                 "ticketweb.com": "Ticketmaster", "eventbrite.com": "Eventbrite"}


def source_label(e: Event) -> str:
    """Where the listing lives, in words people know ("Meetup", not "ical")."""
    host = urlsplit(e.url).netloc.lower().removeprefix("www.")
    for domain, label in SOURCE_LABELS.items():
        if host == domain or host.endswith("." + domain):
            return label
    return {"ical": "Calendar", "jsonld": host or "Website"}.get(e.source, e.source.title())


def _safe_url(u: str) -> str:
    return u if u.startswith(("https://", "http://")) else ""


TIER_LABELS = {"metro": "NYC metro", "us": "US", "international": "International", "unknown": "Location unknown"}


def _when(e: Event, tz: ZoneInfo) -> tuple[str, str]:
    """(day label, time label). Conferences are all-day, often multi-day: 'Wed Aug 5 – Sat Aug 8', 'All day'."""
    start = e.start.astimezone(tz)
    if not e.extra.get("all_day"):
        return fmt_day(start), fmt_time(start)
    day = fmt_day(start)
    if e.extra.get("multi_day") and e.end:
        day += " – " + fmt_day(e.end.astimezone(tz))
    return day, "All day"


def event_dict(e: Event, tz: ZoneInfo) -> dict:
    start = e.start.astimezone(tz)
    day, time_label = _when(e, tz)
    tier = e.extra.get("tier", "metro")
    return {
        "tier": tier, "tier_label": TIER_LABELS.get(tier, tier), "place": e.extra.get("place", "") or (
            e.address if tier in ("us", "international") else ""),
        "id": e.id, "title": e.title, "url": _safe_url(e.url), "source": source_label(e),
        "start": start.isoformat(), "day": day, "time": time_label,
        "group": e.group, "category": e.category, "tags": e.tags[:6],
        "venue": e.venue_name, "address": e.address, "area": e.borough, "hood": e.neighborhood,
        "lat": e.lat, "lon": e.lon, "price": e.price, "image": _safe_url(e.image),
        "desc": e.description[:280],
    }


def rejection_dict(r, tz: ZoneInfo) -> dict:
    start = datetime.fromisoformat(r["start"]).astimezone(tz) if r["start"] else None
    return {
        "title": r["title"], "url": _safe_url(r["url"]), "source": r["source"], "reason": r["reason"],
        "reason_label": REASON_LABELS.get(r["reason"], r["reason"]), "detail": r["detail"],
        "day": fmt_day(start) if start else "", "venue": r["venue_name"],
    }


def _json_for_script(obj) -> str:
    # Third-party strings must not be able to close the <script> tag: encode every "<" (valid JSON escape).
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render(cfg: Config, store: Store, out_path: Path | None = None, now: datetime | None = None) -> Path:
    tz = ZoneInfo(cfg.region.timezone)
    now = now or datetime.now(UTC)
    events = [event_dict(e, tz) for e in store.events(since=now - timedelta(hours=6))]
    rejections = [rejection_dict(r, tz) for r in store.rejections()]
    groups = {g: (spec or {}).get("label", g.title()) for g, spec in cfg.groups.items()}
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    html = env.get_template("index.html.j2").render(
        generated=fmt_day_time(now.astimezone(tz)),
        region=cfg.region.name.upper(),
        count=len(events),
        summary=format_summary(store),
        data_json=_json_for_script({
            "events": events, "rejections": rejections, "groups": groups,
            "categories": [{"id": c.id, "group": c.group} for c in cfg.categories],
            "center": list(cfg.region.center), "home": cfg.places.get("home"),
            "map": {**DEFAULT_MAP, **{k: v for k, v in cfg.web.get("map", {}).items() if _safe_url(str(v))}},
        }),
    )
    out = out_path or cfg.output_dir / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
