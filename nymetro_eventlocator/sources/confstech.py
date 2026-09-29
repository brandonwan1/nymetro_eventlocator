"""confs.tech open conference data (github.com/tech-conferences/conference-data, crowd-sourced, MIT licensed).

config:
  confstech:
    topics: { <confs.tech topic>: <interest id>, <another topic>: "" }
Each topic maps to a category hint ("" = keep only if a keyword rule matches). Topics are the file names under
conferences/<year>/ in the confs.tech repository. With no topics configured, nothing is fetched.
Files are static JSON on raw.githubusercontent.com (no robots.txt; 5 s default delay).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from nymetro_eventlocator.http.polite import FetchError, PoliteClient
from nymetro_eventlocator.models import Event, Rejection
from nymetro_eventlocator.sources.base import Source, SourceResult, register_source

BASE = "https://raw.githubusercontent.com/tech-conferences/conference-data/main/conferences"


def to_event(item: dict, topic: str, tz: ZoneInfo) -> Event:
    start_d = date.fromisoformat(item["startDate"])
    end_d = date.fromisoformat(item.get("endDate") or item["startDate"])
    location = ", ".join(x for x in (item.get("city"), item.get("country")) if x)
    e = Event(
        source="confstech",
        source_id=f"{item['name']}|{item['startDate']}",
        title=item["name"],
        start=datetime.combine(start_d, time(9, 0), tz),  # dates only; shown as all-day
        end=datetime.combine(end_d, time(18, 0), tz),
        url=item.get("url", ""),
        description=f"{topic} conference" + (f" · CFP until {item['cfpEndDate']}" if item.get("cfpEndDate") else ""),
        address=location,
        is_online=bool(item.get("online")) and not item.get("city"),
    )
    # The description is generated here ("networking conference"), not real text: classify by title only,
    # or the topic word would match unrelated keywords (e.g. social "networking").
    e.extra.update({"topic": topic, "all_day": True, "multi_day": (end_d - start_d).days > 0, "title_only": True})
    return e


def parse(data: list, topic: str, tz: ZoneInfo, today: date, horizon: date) -> SourceResult:
    res = SourceResult()
    for item in data:
        try:
            e = to_event(item, topic, tz)
        except (KeyError, ValueError, TypeError) as err:
            stub = Event("confstech", str(item.get("name")), str(item.get("name") or "(untitled)"),
                         datetime.now(tz), item.get("url", "") if isinstance(item, dict) else "")
            res.rejections.append(Rejection(stub, "parse", "parse_error", str(err)))
            continue
        if e.end.date() < today or e.start.date() > horizon:
            continue
        res.events.append(e)
    return res


@register_source("confstech")
class ConfsTechSource(Source):
    access = "feed"
    tos_note = "Open data (MIT) published for reuse at github.com/tech-conferences/conference-data. Checked 2026-09-25."

    def fetch(self, client: PoliteClient) -> SourceResult:
        tz = ZoneInfo(self.cfg.region.timezone)
        today = datetime.now(tz).date()
        horizon = today + timedelta(days=int(self.settings.get("days", 400)))
        out = SourceResult()
        topics: dict = self.settings.get("topics") or {}
        for year in sorted({today.year, horizon.year}):
            for topic, hint in topics.items():
                url = f"{BASE}/{year}/{topic}.json"
                try:
                    resp = client.get(url)
                except FetchError as e:
                    out.errors.append(f"{year}/{topic}: {e}")
                    out.incomplete.add(topic)
                    continue
                if resp.status == 404:
                    continue  # no file for that year/topic yet
                if resp.status != 200:
                    out.errors.append(f"{year}/{topic}: HTTP {resp.status}")
                    out.incomplete.add(topic)
                    continue
                r = parse(resp.json(), topic, tz, today, horizon)
                for e in r.events:
                    e.extra["feed"] = topic
                    if hint:
                        e.extra["category_hint"] = hint
                out.events += r.events
                out.rejections += r.rejections
        return out
