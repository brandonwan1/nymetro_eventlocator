"""Eventbrite API v3 (official), by organizer or venue id. Public search was retired in 2020.

config:
  eventbrite:
    token: ${EVENTBRITE_TOKEN}          # private token from eventbrite.com/platform/api-keys
    organizers: [ { name: example-venue, id: "123456" } ]
    venues: []
API terms (2026-09-22): 1000 calls/hour; show title + direct Eventbrite link; store future events only.
"""

from __future__ import annotations

from datetime import datetime, UTC

from dateutil.parser import isoparse

from nymetro_eventlocator.http.polite import FetchError, PoliteClient
from nymetro_eventlocator.models import Event, Rejection
from nymetro_eventlocator.sources.base import Source, SourceResult, register_source
from nymetro_eventlocator.sources.ical import clean_text

API = "https://www.eventbriteapi.com/v3"
MAX_PAGES = 5


def to_event(item: dict, follow: str) -> Event:
    venue = item.get("venue") or {}
    addr = venue.get("address") or {}
    avail = item.get("ticket_availability") or {}
    price = "Free" if item.get("is_free") else ((avail.get("minimum_ticket_price") or {}).get("display") or "")
    e = Event(
        source="eventbrite",
        source_id=str(item["id"]),
        title=(item.get("name") or {}).get("text") or "(untitled)",
        start=isoparse(item["start"]["utc"]),
        end=isoparse(item["end"]["utc"]) if item.get("end", {}).get("utc") else None,
        url=item.get("url", ""),
        description=clean_text((item.get("description") or {}).get("text") or item.get("summary") or ""),
        venue_name=venue.get("name") or "",
        address=addr.get("localized_address_display") or "",
        lat=float(addr["latitude"]) if addr.get("latitude") else None,
        lon=float(addr["longitude"]) if addr.get("longitude") else None,
        is_online=bool(item.get("online_event")),
        price=price,
        image=(item.get("logo") or {}).get("url") or "",
    )
    e.extra["follow"] = follow
    return e


def parse_page(data: dict, follow: str) -> SourceResult:
    res = SourceResult()
    for item in data.get("events", []):
        try:
            e = to_event(item, follow)
        except (KeyError, ValueError, TypeError) as err:
            stub = Event("eventbrite", str(item.get("id", "?")), (item.get("name") or {}).get("text") or "(untitled)",
                         datetime.now(UTC), item.get("url", ""))
            res.rejections.append(Rejection(stub, "parse", "parse_error", str(err)))
            continue
        if item.get("status") in ("canceled", "cancelled") or item.get("cancelled"):
            res.rejections.append(Rejection(e, "parse", "cancelled", "canceled"))
            continue
        res.events.append(e)
    return res


@register_source("eventbrite")
class EventbriteSource(Source):
    access = "official_api"
    tos_note = "API Terms reviewed 2026-09-22 (site scraping is forbidden; API allowed). See docs/sources-compliance.md"

    def fetch(self, client: PoliteClient) -> SourceResult:
        token = self.settings.get("token") or ""
        if not token:
            return SourceResult(errors=["EVENTBRITE_TOKEN is not set"], incomplete={"*"})
        out = SourceResult()
        follows = [("organizers", o) for o in self.settings.get("organizers") or []] + \
                  [("venues", v) for v in self.settings.get("venues") or []]
        for kind, f in follows:
            params = {"status": "live", "order_by": "start_asc", "expand": "venue,ticket_availability"}
            out.incomplete.add(f["name"])  # removed once the last page is read
            for _ in range(MAX_PAGES):
                try:
                    resp = client.get(f"{API}/{kind}/{f['id']}/events/", params=params,
                                      headers={"Authorization": f"Bearer {token}"}, conditional=False)
                except FetchError as e:
                    out.errors.append(f"{f['name']}: {e}")
                    break
                if resp.status != 200:
                    out.errors.append(f"{f['name']}: HTTP {resp.status}")
                    break
                data = resp.json()
                r = parse_page(data, f["name"])
                for e in r.events:
                    if f.get("category"):
                        e.extra["category_hint"] = f["category"]
                out.events += r.events
                out.rejections += r.rejections
                pag = data.get("pagination") or {}
                if not pag.get("has_more_items") or not pag.get("continuation"):
                    out.incomplete.discard(f["name"])
                    break
                params = {**params, "continuation": pag["continuation"]}
        return out
