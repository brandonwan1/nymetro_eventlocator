"""Hand-entered events, for things no allowed source lists. Nothing is fetched.

config:
  manual:
    events:
      - { title: "Example Festival", start: 2027-08-05, end: 2027-08-08, url: "https://example.org/",
          location: "Las Vegas, NV, USA", category: <interest id>, note: "dates from the official site, 2027-01-10" }
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from nymetro_eventlocator.http.polite import PoliteClient
from nymetro_eventlocator.models import Event, Rejection
from nymetro_eventlocator.sources.base import Source, SourceResult, register_source


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


@register_source("manual")
class ManualSource(Source):
    access = "manual"
    tos_note = "Typed in by hand from official sites; nothing is fetched."

    def fetch(self, client: PoliteClient) -> SourceResult:
        tz = ZoneInfo(self.cfg.region.timezone)
        out = SourceResult()
        for item in self.settings.get("events") or []:
            try:
                start, end = _d(item["start"]), _d(item.get("end") or item["start"])
                e = Event(
                    source="manual", source_id=f"{item['title']}|{start.isoformat()}", title=item["title"],
                    start=datetime.combine(start, time(9, 0), tz), end=datetime.combine(end, time(18, 0), tz),
                    url=item.get("url", ""), description=item.get("note", ""), address=item.get("location", ""),
                )
            except (KeyError, ValueError, TypeError) as err:
                stub = Event("manual", str(item.get("title")), str(item.get("title") or "(untitled)"), datetime.now(tz), "")
                out.rejections.append(Rejection(stub, "parse", "parse_error", f"manual entry: {err}"))
                continue
            e.extra.update({"all_day": True, "multi_day": end > start})
            if item.get("category"):
                e.extra["category_hint"] = item["category"]
            out.events.append(e)
        return out
