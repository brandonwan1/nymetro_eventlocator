"""Generic structured-data source: schema.org Event items (JSON-LD or microdata) on pages you list.

config:
  jsonld:
    pages:
      - { name: some-venue, url: "https://venue.example/events", category: concert, venue: "Venue", address: "1 Main St, Brooklyn" }
`category` is a hint (used only when no keyword matches); `venue`/`address` fill in missing location fields.
Every page must be audited first (robots.txt + ToS) and noted in docs/sources-compliance.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from collections.abc import Iterator
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import extruct
from dateutil.parser import isoparse

from nymetro_eventlocator.http.polite import FetchError, PoliteClient
from nymetro_eventlocator.models import Event, Rejection
from nymetro_eventlocator.sources.base import Source, SourceResult, register_source
from nymetro_eventlocator.sources.ical import clean_text

EVENT_TYPES = {
    "Event", "MusicEvent", "DanceEvent", "SocialEvent", "SportsEvent", "ComedyEvent", "Festival", "EducationEvent",
    "ExhibitionEvent", "LiteraryEvent", "TheaterEvent", "FoodEvent", "BusinessEvent", "ChildrensEvent", "VisualArtsEvent",
}


def _types(item: dict) -> set[str]:
    t = item.get("@type", [])
    return {x.rsplit("/", 1)[-1] for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)}


def iter_events(node: Any) -> Iterator[dict]:
    """Walk @graph, ItemList and nested structures, yielding every Event-typed dict."""
    if isinstance(node, list):
        for n in node:
            yield from iter_events(n)
    elif isinstance(node, dict):
        if _types(node) & EVENT_TYPES:
            yield node
            for sub in _as_list(node.get("subEvent")):
                yield from iter_events(sub)
            return
        for key in ("@graph", "itemListElement", "item", "mainEntity", "event", "events"):
            if key in node:
                yield from iter_events(node[key])


def _as_list(v) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _first(v) -> Any:
    lst = _as_list(v)
    return lst[0] if lst else None


def _text(v) -> str:
    v = _first(v)
    if isinstance(v, dict):
        v = v.get("name") or v.get("@value") or v.get("url") or ""
    return str(v or "").strip()


def _address(addr) -> str:
    addr = _first(addr)
    if isinstance(addr, dict):
        parts = [addr.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
        return ", ".join(str(p).strip() for p in parts if p)
    return str(addr or "").strip()


def _price(offers) -> str:
    for o in _as_list(offers):
        if not isinstance(o, dict):
            continue
        p = o.get("price", o.get("lowPrice"))
        if p in (None, ""):
            continue
        try:
            if float(p) == 0:
                return "Free"
        except (TypeError, ValueError):
            return str(p)
        cur = o.get("priceCurrency", "USD")
        return f"${float(p):g}" if cur == "USD" else f"{p} {cur}"
    return ""


def _dt(v, tz: ZoneInfo) -> datetime | None:
    s = _text(v)
    if not s:
        return None
    d = isoparse(s)
    return d if d.tzinfo else d.replace(tzinfo=tz)


def to_event(item: dict, *, source: str, page: dict, base_url: str, tz: ZoneInfo) -> Event:
    loc = _first(item.get("location")) or {}
    venue = address = ""
    lat = lon = None
    online = "OnlineEventAttendanceMode" in _text(item.get("eventAttendanceMode"))
    if isinstance(loc, dict):
        if "VirtualLocation" in _types(loc):
            online = True
        venue = _text(loc.get("name"))
        address = _address(loc.get("address"))
        geo = loc.get("geo") or {}
        if isinstance(geo, dict) and geo.get("latitude") not in (None, ""):
            lat, lon = float(geo["latitude"]), float(geo["longitude"])
    elif isinstance(loc, str):
        venue = loc
    url = urljoin(base_url, _text(item.get("url"))) if _text(item.get("url")) else base_url
    start = _dt(item.get("startDate"), tz)
    if start is None:
        raise ValueError("no startDate")
    return Event(
        source=source,
        source_id=f"{page['name']}:{url}#{start.isoformat()}",
        title=_text(item.get("name")) or "(untitled)",
        start=start,
        end=_dt(item.get("endDate"), tz),
        url=url,
        description=clean_text(_text(item.get("description"))),
        venue_name=venue or page.get("venue", ""),
        address=address or page.get("address", ""),
        lat=lat,
        lon=lon,
        is_online=online,
        price=_price(item.get("offers")),
        image=_text(item.get("image")),
    )


def parse_page(html: str, *, source: str, page: dict, base_url: str, tz: ZoneInfo,
               now: datetime | None = None, horizon_days: int = 90) -> SourceResult:
    now = now or datetime.now(tz)
    res = SourceResult()
    data = extruct.extract(html, base_url=base_url, syntaxes=["json-ld", "microdata"], uniform=True, errors="ignore")
    seen: set[str] = set()
    for item in iter_events(data.get("json-ld", []) + data.get("microdata", [])):
        try:
            e = to_event(item, source=source, page=page, base_url=base_url, tz=tz)
        except (ValueError, TypeError, KeyError) as err:
            stub = Event(source, f"{page['name']}:{_text(item.get('name'))}", _text(item.get("name")) or "(untitled)", now, base_url)
            res.rejections.append(Rejection(stub, "parse", "parse_error", str(err)))
            continue
        if e.source_id in seen or (e.end or e.start) < now or e.start > now + timedelta(days=horizon_days):
            continue
        seen.add(e.source_id)
        status = _text(item.get("eventStatus"))
        if "Cancelled" in status or "Postponed" in status:
            res.rejections.append(Rejection(e, "parse", "cancelled", status.rsplit("/", 1)[-1]))
            continue
        e.extra["page"] = page["name"]
        if page.get("category"):
            e.extra["category_hint"] = page["category"]
        res.events.append(e)
    return res


@register_source("jsonld")
class JsonLdSource(Source):
    access = "html"
    tos_note = "Only pages individually audited (robots.txt + ToS) and listed in docs/sources-compliance.md"

    def fetch(self, client: PoliteClient) -> SourceResult:
        tz = ZoneInfo(self.cfg.region.timezone)
        out = SourceResult()
        for page in self.settings.get("pages") or []:
            try:
                resp = client.get(page["url"])
            except FetchError as e:
                out.errors.append(f"{page['name']}: {e}")
                out.incomplete.add(page["name"])
                continue
            if resp.status != 200:
                out.errors.append(f"{page['name']}: HTTP {resp.status}")
                out.incomplete.add(page["name"])
                continue
            r = parse_page(resp.text, source=self.name, page=page, base_url=resp.url, tz=tz)
            if not r.events and not r.rejections:
                out.errors.append(f"{page['name']}: no schema.org events found on the page")
            out.events += r.events
            out.rejections += r.rejections
        return out
