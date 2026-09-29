"""Generic iCal (.ics) source: Meetup group calendars, Luma calendars, venue calendars.

config:
  ical:
    feeds:
      - { name: my-group, url: "https://www.meetup.com/<group-name>/events/ical/", category: <interest id> }
`category` is optional: used only when no keyword rule matches (a hint, not an override).

  ical:
    details: { hosts: [www.meetup.com], max_per_run: 100, refresh_days: 7 }
Feeds without a LOCATION (Meetup) get venue/address from the event's own page (schema.org JSON-LD),
fetched through the gateway at most once per `refresh_days` per event.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from urllib.parse import urlsplit

import extruct
from icalendar import Calendar

from nymetro_eventlocator.http.polite import FetchError, PoliteClient
from nymetro_eventlocator.models import Event, Rejection
from nymetro_eventlocator.sources.base import Source, SourceResult, register_source

MAX_DESCRIPTION = 2000
_VENUE_PARENS = re.compile(r"^\s*(?P<venue>[^()]+?)\s*\((?P<addr>.+)\)\s*$")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Luma: "Address:\n<venue>\n<city>\n<country>\n\nHosted by ..." — the address repeats LOCATION.
_ADDRESS_BLOCK = re.compile(r"^\s*Address:\s*\n(?:.+\n?)*?(?:\n\s*\n|\Z)", re.IGNORECASE)
_INFO_LINK = re.compile(r"(?:Get up-to-date information at|More info|Details|RSVP)[^:]{0,20}:\s*(https?://\S+)", re.IGNORECASE)


def clean_text(s: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = _MD_LINK.sub(r"\1", s).replace("**", "").replace("\\n", "\n")
    return re.sub(r"[ \t]+", " ", s).strip()[:MAX_DESCRIPTION]


def split_location(loc: str) -> tuple[str, str]:
    """'Venue (123 St, New York, NY)' or 'Venue, 123 St, New York' -> (venue, address)."""
    loc = " ".join((loc or "").split())
    if not loc:
        return "", ""
    m = _VENUE_PARENS.match(loc)
    if m:
        return m["venue"], m["addr"]
    head, _, rest = loc.partition(",")
    if rest and not re.match(r"^\d", head.strip()):
        return head.strip(), loc
    return "", loc


def to_datetime(v, tz: ZoneInfo) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=tz)
    if isinstance(v, date):
        return datetime.combine(v, time(0), tz)
    raise ValueError(f"unsupported date value {v!r}")


def parse_ics(data: bytes, *, source: str, feed_name: str, feed_url: str, tz: ZoneInfo,
              category_hint: str = "", now: datetime | None = None, horizon_days: int = 90) -> SourceResult:
    now = now or datetime.now(tz)
    horizon = now + timedelta(days=horizon_days)
    res = SourceResult()
    cal = Calendar.from_ical(data)
    for comp in cal.walk("VEVENT"):
        title = str(comp.get("SUMMARY", "")).strip()
        try:
            start = to_datetime(comp.decoded("DTSTART"), tz)
            end = to_datetime(comp.decoded("DTEND"), tz) if comp.get("DTEND") else None
        except (KeyError, ValueError) as e:
            stub = Event(source, str(comp.get("UID", title)), title or "(untitled)", now, feed_url)
            res.rejections.append(Rejection(stub, "parse", "parse_error", f"bad date: {e}"))
            continue
        if (end or start) < now or start > horizon:
            continue  # past or too far out: not stored at all (some terms forbid keeping past events)
        raw_desc = str(comp.get("DESCRIPTION", ""))
        url = str(comp.get("URL", ""))
        if not url:  # Luma puts the event link in the description
            m = _INFO_LINK.search(raw_desc)
            url = m.group(1) if m else ""
            raw_desc = _INFO_LINK.sub("", raw_desc, count=1)
        venue, address = split_location(str(comp.get("LOCATION", "")))
        lat = lon = None
        if comp.get("GEO"):
            g = comp.get("GEO")
            lat, lon = float(g.latitude), float(g.longitude)
        e = Event(
            source=source,
            source_id=f"{feed_name}:{comp.get('UID', title + start.isoformat())}",
            title=title or "(untitled)",
            start=start,
            end=end,
            url=url or feed_url,
            description=_ADDRESS_BLOCK.sub("", clean_text(raw_desc), count=1).strip(),
            venue_name=venue,
            address=address,
            lat=lat,
            lon=lon,
        )
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            res.rejections.append(Rejection(e, "parse", "cancelled", "STATUS:CANCELLED"))
            continue
        e.extra["feed"] = feed_name
        if category_hint:
            e.extra["category_hint"] = category_hint
        if comp.get("RRULE"):
            e.extra["recurring"] = True  # first occurrence only; feeds we use list instances individually
        res.events.append(e)
    return res


@register_source("ical")
class IcalSource(Source):
    access = "feed"
    tos_note = ("Calendar subscription feeds the sites publish (Meetup /events/ical, Luma 'Add iCal Subscription'). "
                "Audited 2026-09-22, see docs/sources-compliance.md")

    def fetch(self, client: PoliteClient) -> SourceResult:
        tz = ZoneInfo(self.cfg.region.timezone)
        out = SourceResult()
        for feed in self.settings.get("feeds") or []:
            try:
                resp = client.get(feed["url"])
                if resp.status != 200:
                    out.errors.append(f"{feed['name']}: HTTP {resp.status}")
                    out.incomplete.add(feed["name"])
                    continue
                r = parse_ics(resp.content, source=self.name, feed_name=feed["name"], feed_url=feed["url"],
                              tz=tz, category_hint=feed.get("category", ""), horizon_days=int(feed.get("horizon_days", 90)))
            except (FetchError, ValueError) as e:
                out.errors.append(f"{feed['name']}: {e}")
                out.incomplete.add(feed["name"])
                continue
            out.events += r.events
            out.rejections += r.rejections
        details = self.settings.get("details") or {}
        if details.get("hosts"):
            self._add_details(client, out, details)
        return out

    def _add_details(self, client: PoliteClient, out: SourceResult, details: dict) -> None:
        hosts = set(details["hosts"])
        budget = int(details.get("max_per_run", 100))
        max_age = float(details.get("refresh_days", 7))
        for e in out.events:
            if e.venue_name or e.address or e.lat is not None or urlsplit(e.url).netloc not in hosts:
                continue
            row = client.store.detail_get(e.url, max_age)
            if row is None:
                if budget <= 0:
                    continue
                budget -= 1
                try:
                    resp = client.get(e.url, conditional=False)
                except FetchError as err:
                    out.errors.append(f"details {e.url}: {err}")
                    continue
                if resp.status != 200:
                    continue
                loc = page_location(resp.text, resp.url)
                client.store.detail_put(e.url, *loc)
                row = client.store.detail_get(e.url, max_age)
            e.venue_name, e.address = row["venue_name"], row["address"]
            e.lat, e.lon = row["lat"], row["lon"]
            e.is_online = e.is_online or bool(row["is_online"])


def page_location(html_text: str, url: str) -> tuple[str, str, float | None, float | None, bool]:
    """(venue, address, lat, lon, online) from the first schema.org Event on an event page."""
    from nymetro_eventlocator.sources.jsonld import _address, _first, _text, _types, iter_events

    data = extruct.extract(html_text, base_url=url, syntaxes=["json-ld"], uniform=True, errors="ignore")
    for item in iter_events(data.get("json-ld", [])):
        loc = _first(item.get("location")) or {}
        online = "OnlineEventAttendanceMode" in _text(item.get("eventAttendanceMode"))
        if not isinstance(loc, dict):
            return "", str(loc), None, None, online
        online = online or "VirtualLocation" in _types(loc)
        geo = loc.get("geo") if isinstance(loc.get("geo"), dict) else {}
        lat = float(geo["latitude"]) if geo.get("latitude") not in (None, "") else None
        lon = float(geo["longitude"]) if geo.get("longitude") not in (None, "") else None
        address = re.sub(r",\s*,", ",", _address(loc.get("address")))  # Meetup writes "242 Butler street,, Kings County"
        return _text(loc.get("name")), address, lat, lon, online
    return "", "", None, None, False
