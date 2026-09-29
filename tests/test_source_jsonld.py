from datetime import datetime, UTC
from pathlib import Path
from zoneinfo import ZoneInfo

from nymetro_eventlocator.sources.jsonld import parse_page

FIX = Path(__file__).parent / "fixtures" / "jsonld"
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def parse(**page):
    page = {"name": "sample", **page}
    return parse_page((FIX / "venue_sample.html").read_text(encoding="utf-8"), source="jsonld", page=page,
                      base_url="https://venue.test/events", tz=NY, now=NOW)


def test_extracts_events_from_graph_itemlist_and_microdata():
    res = parse()
    by_title = {e.title: e for e in res.events}
    assert set(by_title) == {"Trivia Tuesdays", "Free day party", "Livestream", "Microdata Game Night"}
    t = by_title["Trivia Tuesdays"]
    assert t.start.isoformat() == "2026-10-06T22:00:00-04:00"
    assert (t.venue_name, t.address) == ("Sample Venue", "100 Johnson Ave, Brooklyn, NY, 11237")
    assert (t.lat, t.lon, t.price, t.image) == (40.7094, -73.9232, "$25", "https://venue.test/img/1.jpg")
    assert t.description == "Resident hosts & guests"


def test_relative_url_price_free_and_naive_time():
    e = next(e for e in parse().events if e.title == "Free day party")
    assert e.url == "https://venue.test/e/day-party"
    assert e.price == "Free"
    assert e.start.tzinfo is not None and e.start.utcoffset().total_seconds() == -4 * 3600


def test_online_cancelled_past_and_bad_items():
    res = parse()
    assert next(e for e in res.events if e.title == "Livestream").is_online
    reasons = {(r.event.title, r.reason) for r in res.rejections}
    assert ("Cancelled show", "cancelled") in reasons
    assert ("No date", "parse_error") in reasons
    assert all(e.title != "Old show" for e in res.events)


def test_page_defaults_and_hint():
    res = parse(venue="Hall X", address="1 Main St", category="board-games")
    mat = next(e for e in res.events if e.title == "Microdata Game Night")
    assert (mat.venue_name, mat.address, mat.extra["category_hint"]) == ("Hall X", "1 Main St", "board-games")


def test_page_without_structured_data():
    res = parse_page("<html><body>nothing</body></html>", source="jsonld", page={"name": "x"},
                     base_url="https://x.test", tz=NY, now=NOW)
    assert res.events == [] and res.rejections == []
