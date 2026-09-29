"""Ticketmaster Discovery API v2 (official). Needs a free key: developer.ticketmaster.com.

config:
  ticketmaster:
    api_key: ${TICKETMASTER_API_KEY}
    searches:                  # circles to search; defaults to 15 mi around region.center
      - { latlong: [40.7128, -74.0060], radius_miles: 20 }
    classification: music
    max_pages: 5               # 200 events per page; API deep-paging limit is 1000
    days: 90                   # how far ahead
    window_days: 30            # query in date windows so no window exceeds the 1000 cap
Terms (2026-09-22): may not cache Event Content beyond reasonable periods -> past events are purged daily.
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse

from nymetro_eventlocator.http.polite import FetchError, PoliteClient
from nymetro_eventlocator.models import Event, Rejection
from nymetro_eventlocator.sources.base import Source, SourceResult, register_source

API = "https://app.ticketmaster.com/discovery/v2/events.json"
PAGE_SIZE = 200


def _start(dates: dict, tz: ZoneInfo) -> datetime | None:
    s = dates.get("start", {})
    if s.get("dateTime"):
        return isoparse(s["dateTime"])
    if s.get("localDate"):
        t = s.get("localTime") or "00:00:00"
        return isoparse(f"{s['localDate']}T{t}").replace(tzinfo=tz)
    return None


def to_event(item: dict, tz: ZoneInfo) -> Event:
    start = _start(item.get("dates", {}), tz)
    if start is None:
        raise ValueError("no start date")
    venue = (item.get("_embedded", {}).get("venues") or [{}])[0]
    loc = venue.get("location") or {}
    addr = ", ".join(x for x in (venue.get("address", {}).get("line1"), venue.get("city", {}).get("name"),
                                 venue.get("state", {}).get("stateCode"), venue.get("postalCode")) if x)
    prices = item.get("priceRanges") or []
    price = f"${prices[0]['min']:g}+" if prices and prices[0].get("min") is not None else ""
    genres = []
    for c in item.get("classifications") or []:
        for k in ("segment", "genre", "subGenre"):
            name = (c.get(k) or {}).get("name")
            if name and name != "Undefined" and name not in genres:
                genres.append(name)
    images = sorted(item.get("images") or [], key=lambda i: i.get("width", 0), reverse=True)
    desc = " · ".join(x for x in (", ".join(genres), item.get("info") or "", item.get("pleaseNote") or "") if x)
    e = Event(
        source="ticketmaster",
        source_id=item["id"],
        title=item.get("name", "(untitled)"),
        start=start,
        url=item.get("url", ""),
        description=desc[:2000],
        venue_name=venue.get("name", ""),
        address=addr,
        lat=float(loc["latitude"]) if loc.get("latitude") else None,
        lon=float(loc["longitude"]) if loc.get("longitude") else None,
        price=price,
        image=images[0]["url"] if images else "",
        tags=genres,
    )
    e.extra["category_hint"] = "concert"
    e.extra["title_only"] = True  # info/pleaseNote are venue policies ("no party larger than 6")
    status = item.get("dates", {}).get("status", {}).get("code", "")
    if status in ("cancelled", "postponed"):
        e.extra["status"] = status
    return e


def parse_page(data: dict, tz: ZoneInfo) -> SourceResult:
    res = SourceResult()
    for item in data.get("_embedded", {}).get("events", []):
        try:
            e = to_event(item, tz)
        except (ValueError, KeyError, TypeError) as err:
            stub = Event("ticketmaster", item.get("id", "?"), item.get("name", "(untitled)"), datetime.now(UTC), item.get("url", ""))
            res.rejections.append(Rejection(stub, "parse", "parse_error", str(err)))
            continue
        if e.extra.get("status"):
            res.rejections.append(Rejection(e, "parse", "cancelled", e.extra["status"]))
            continue
        res.events.append(e)
    return res


@register_source("ticketmaster")
class TicketmasterSource(Source):
    access = "official_api"
    tos_note = "Discovery API terms reviewed 2026-09-22: no ban on combining sources; don't keep past events. See docs/sources-compliance.md"

    def fetch(self, client: PoliteClient) -> SourceResult:
        key = self.settings.get("api_key") or ""
        if not key:
            return SourceResult(errors=["TICKETMASTER_API_KEY is not set"], incomplete={"*"})
        tz = ZoneInfo(self.cfg.region.timezone)
        now = datetime.now(UTC).replace(microsecond=0)
        searches = self.settings.get("searches") or [{"latlong": list(self.cfg.region.center), "radius_miles": 15}]
        out = SourceResult()
        seen: set[str] = set()
        days, window = int(self.settings.get("days", 90)), int(self.settings.get("window_days", 30))
        windows = [(now + timedelta(days=d), now + timedelta(days=min(d + window, days))) for d in range(0, days, window)]
        for search, (w_start, w_end) in ((s, w) for s in searches for w in windows):
            lat, lon = search["latlong"]
            for page in range(int(self.settings.get("max_pages", 5))):
                params = {
                    "apikey": key, "latlong": f"{lat},{lon}", "radius": search.get("radius_miles", 15), "unit": "miles",
                    "classificationName": self.settings.get("classification", "music"), "size": PAGE_SIZE, "page": page,
                    "sort": "date,asc",
                    # Windows keep each query under the API's 1000-result deep-paging cap
                    "startDateTime": w_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endDateTime": w_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                try:
                    resp = client.get(API, params=params, secrets=(key,))
                except FetchError as e:
                    out.errors.append(str(e))
                    out.incomplete.add("*")
                    break
                if resp.status != 200:
                    out.errors.append(f"HTTP {resp.status}")
                    out.incomplete.add("*")
                    break
                data = resp.json()
                r = parse_page(data, tz)
                out.events += [e for e in r.events if e.source_id not in seen]
                seen.update(e.source_id for e in r.events)
                out.rejections += r.rejections
                info = data.get("page", {})
                if info.get("number", 0) + 1 >= info.get("totalPages", 0):
                    break
                if (page + 1) * PAGE_SIZE >= 1000 or page + 1 >= int(self.settings.get("max_pages", 5)):
                    out.incomplete.add("*")  # results were cut off, so absence proves nothing
                    out.errors.append(f"window {w_start:%Y-%m-%d} truncated at {(page + 1) * PAGE_SIZE} results")
                    break
        return out
