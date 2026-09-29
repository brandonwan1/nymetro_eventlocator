"""`nymetro_eventlocator demo`: build the web page from made-up events, so people can see what the tool makes
before setting anything up. No config, network, keys or database are needed; nothing is fetched.

The events go through the real pipeline (classify -> locate -> dedupe -> store -> render), so the page shows
real behavior: sections, the map, duplicates merged, and a "Didn't qualify" tab. All names are fictional
("Example ...") and the interests are generic, so the demo reflects no one's personal groups or hobbies.
Dates are relative to today, so the demo never goes stale.

`demo --live` instead fetches real upcoming events, once, from the public library calendars in LIVE_FEEDS:
general-interest, official iCal subscriptions, audited in docs/sources-compliance.md. Nothing is stored.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nymetro_eventlocator.config import Config, parse_config
from nymetro_eventlocator.db.store import Store
from nymetro_eventlocator.http.polite import FetchError, PoliteClient
from nymetro_eventlocator.models import Event

# The same interests as examples/config.filtered.yaml (a test keeps the two in step).
DEMO_INTERESTS: dict = {
    "groups": {
        "outdoors": {"label": "Outdoors"},
        "games": {"label": "Games"},
        "talks": {"label": "Talks"},
        "community": {"label": "Community"},
    },
    "categories": [
        {"id": "hiking", "group": "outdoors", "keywords": ["hike", "hiking", "nature walk", "trail walk"]},
        {"id": "yoga", "group": "outdoors", "keywords": ["yoga", "vinyasa", "sound bath"]},
        {"id": "board-games", "group": "games", "keywords": ["board game", "board games", "game night", "chess"]},
        {"id": "trivia", "group": "games", "keywords": ["trivia", "quiz night"]},
        {"id": "tech-talk", "group": "talks", "match_venue": False,
         "keywords": ["tech talk", "lightning talks", "python", "data meetup"]},
        {"id": "community", "group": "community", "fallback": True,
         "keywords": ["meetup", "mixer", "social", "potluck", "book club"]},
    ],
    "exclude_keywords": ["casino", "for kids"],
}

# (title, days from today, hour, venue, (lat, lon) or None, extras)
_EVENTS: list[tuple] = [
    ("Sunrise yoga on the lawn", 1, 7, "Example Lawn, Prospect Park", (40.6602, -73.9690), {"price": "Free"}),
    ("Palisades group hike", 2, 9, "Example Trailhead, Fort Lee", (40.8510, -73.9701), {}),
    ("Board game night", 1, 19, "Example Game Café, Astoria", (40.7644, -73.9235), {"price": "$5"}),
    ("Pub trivia: movies edition", 3, 20, "Example Taproom, Hoboken", (40.7440, -74.0324), {}),
    ("Lightning talks: data on a laptop", 4, 18, "Example Hall, Flatiron", (40.7410, -73.9897), {"price": "Free"}),
    ("Python meetup: testing in practice", 9, 18, "Example Library, Jersey City", (40.7178, -74.0431), {"price": "Free"}),
    ("Chess in the park", 5, 14, "Example Plaza, Washington Square", (40.7308, -73.9973), {"price": "Free"}),
    ("Nature walk and birding", 6, 8, "Example Nature Center, Long Island", (40.8700, -73.4300), {}),
    ("Neighborhood potluck", 7, 18, "Example Community Room, Bushwick", (40.6944, -73.9213), {}),
    ("Sound bath and stretch", 8, 19, "Example Studio, White Plains", (41.0340, -73.7629), {"price": "$20"}),
    ("Quiz night", 10, 20, "Example Pub, Harlem", (40.8116, -73.9465), {}),
    ("Book club: short stories", 11, 19, "Example Bookshop, Park Slope", (40.6710, -73.9814), {}),
    ("Saturday trail walk", 12, 10, "Example Reservation, New Jersey", (40.7330, -74.3400), {}),
    ("Game night: strategy classics", 13, 19, "Example Game Café, Astoria", (40.7644, -73.9235), {}),
    ("New-neighbor mixer", 14, 18, "Example Roof Deck, Long Island City", (40.7447, -73.9485), {}),
    # The same event listed on a second site: kept once; the second listing shows as a duplicate under "Didn't qualify".
    ("Board Game Night!", 1, 19, "Example Game Cafe", (40.7645, -73.9236), {"source": "demo-listings"}),
    # These don't qualify, and show up under "Didn't qualify" with the reason:
    ("Group hike near Philadelphia", 3, 12, "Example Trailhead, Philadelphia", (39.9526, -75.1652), {}),   # outside the region
    ("Online book club", 4, 20, "Online (Zoom)", None, {}),                                              # online only
    ("Casino bus trip", 5, 9, "Example Plaza", (40.7580, -73.9855), {}),                                  # excluded word
    ("Tax preparation seminar", 6, 18, "Example Office", (40.7527, -73.9772), {}),                        # matches no interest
    ("Trivia night CANCELLED", 2, 20, "Example Pub, Harlem", (40.8116, -73.9465), {}),                     # called off
]


def demo_config(root: Path, output_dir: Path) -> Config:
    from nymetro_eventlocator.wizard import BASE

    return parse_config({**BASE, **DEMO_INTERESTS, "sources": {}, "output_dir": str(output_dir)}, root=root)


def demo_events(tz: ZoneInfo, today: datetime | None = None) -> list[Event]:
    today = (today or datetime.now(tz)).date()
    out = []
    for i, (title, days, hour, venue, coords, extra) in enumerate(_EVENTS):
        start = datetime.combine(today + timedelta(days=days), time(hour), tz)
        source = extra.get("source", "demo")
        e = Event(source=source, source_id=f"{source}-{i}", title=title, start=start, end=start + timedelta(hours=2),
                  url=f"https://example.com/events/{source}-{i}", venue_name=venue,
                  lat=coords[0] if coords else None, lon=coords[1] if coords else None,
                  price=extra.get("price", ""), description="A made-up event for the demo page.")
        out.append(e)
    return out


def build(out_path: Path) -> tuple[Path, int, int]:
    """Write the demo page to out_path. Returns (path, events shown, events that didn't qualify)."""
    from nymetro_eventlocator.pipeline import process
    from nymetro_eventlocator.render.page import render

    with tempfile.TemporaryDirectory() as tmp:
        cfg = demo_config(Path(tmp), out_path.parent)
        store = Store(":memory:")
        try:
            rep = process(demo_events(ZoneInfo(cfg.region.timezone)), cfg, store, None)
            path = render(cfg, store, out_path)
        finally:
            store.close()
    return path, len(rep.kept), len(rep.rejected)


# --- live mode: real events from public library calendars ------------------------------------------

# Official iCal subscriptions the libraries publish (LibCal "Subscribe"); audited 2026-09-29 (robots.txt allows,
# Crawl-delay 10 s). Their feeds give only a room name as the location, so each event gets its library's address.
LIVE_FEEDS: list[dict] = [
    {"name": "hicksville-library", "label": "Hicksville Public Library",
     "url": "https://hicksvillelibrary.libcal.com/ical_subscribe.php?src=p&cid=18197",
     "address": "169 Jerusalem Ave, Hicksville, NY 11801", "coords": (40.76209, -73.52336)},
    {"name": "west-hempstead-library", "label": "West Hempstead Public Library",
     "url": "https://whplibrary.libcal.com/ical_subscribe.php?src=p&cid=21141",
     "address": "500 Hempstead Ave, West Hempstead, NY 11552", "coords": (40.69616, -73.65423)},
]
LIVE_DAYS = 14


def _fetch_live(client: PoliteClient, feeds: list[dict], tz: ZoneInfo) -> tuple[list[Event], list, list[str]]:
    from nymetro_eventlocator.sources.ical import parse_ics

    events, rejections, report = [], [], []
    for f in feeds:
        try:
            resp = client.get(f["url"])
        except FetchError as e:
            report.append(f"{f['label']}: not fetched ({e})")
            continue
        if resp.status != 200:
            report.append(f"{f['label']}: HTTP {resp.status}")
            continue
        res = parse_ics(resp.content, source="ical", feed_name=f["name"], feed_url=f["url"], tz=tz,
                        horizon_days=LIVE_DAYS)
        for e in res.events:
            room = e.venue_name or e.address
            e.venue_name = f"{f['label']} ({room})" if room and room != f["label"] else f["label"]
            e.address = f["address"]
            if e.lat is None:
                e.lat, e.lon = f["coords"]
        events += res.events
        rejections += res.rejections
        report.append(f"{f['label']}: {len(res.events)} upcoming events")
    return events, rejections, report


def build_live(out_path: Path, client: PoliteClient, feeds: list[dict] | None = None) -> tuple[Path, int, int, list[str]]:
    """Fetch real events once and write the page. Returns (path, events shown, didn't qualify, per-feed report).

    Keep-everything mode (no interests), so every real event that passes the safety filters is shown."""
    from nymetro_eventlocator.pipeline import process
    from nymetro_eventlocator.render.page import render
    from nymetro_eventlocator.wizard import BASE

    with tempfile.TemporaryDirectory() as tmp:
        cfg = parse_config({**BASE, "sources": {}, "output_dir": str(out_path.parent)}, root=Path(tmp))
        store = Store(":memory:")
        try:
            events, source_rejections, report = _fetch_live(client, feeds or LIVE_FEEDS, ZoneInfo(cfg.region.timezone))
            rep = process(events, cfg, store, None)   # no geocoding needed: every event has its library's location
            for r in source_rejections:
                store.record_rejection(r)
            path = render(cfg, store, out_path)
        finally:
            store.close()
    return path, len(rep.kept), len(rep.rejected) + len(source_rejections), report
