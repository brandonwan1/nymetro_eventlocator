from datetime import datetime, UTC
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nymetro_eventlocator.classify import Classifier
from nymetro_eventlocator.config import load_config
from nymetro_eventlocator.sources.base import REGISTRY, load_all
from nymetro_eventlocator.sources.ical import parse_ics, split_location

FIX = Path(__file__).parent / "fixtures" / "ical"
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tests" / "fixtures" / "test_config.yaml"
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def parse(name, **kw):
    return parse_ics((FIX / name).read_bytes(), source="ical", feed_name=name.split(".")[0],
                     feed_url=f"https://feed.test/{name}", tz=NY, now=NOW, **kw)


def test_meetup_group_feed():
    res = parse("meetup_group.ics", category_hint="board-games")
    assert len(res.events) == 1 and res.rejections == []
    e = res.events[0]
    assert "Board Game Night" in e.title
    assert e.start == datetime(2026, 9, 28, 18, 30, tzinfo=NY)
    assert e.url == "https://www.meetup.com/example-board-games/events/316625161/"
    assert e.source_id.startswith("meetup_group:event_316625161@meetup.com")
    assert "**" not in e.description and "](" not in e.description and "\\n" not in e.description


def test_luma_style_feed_fields_and_filtering():
    res = parse("luma_sample.ics")
    titles = [e.title for e in res.events]
    assert titles == ["Thursday Evening Walk", "All-day chess tournament", "Online book chat"]  # past + far dropped
    walk = res.events[0]
    assert (walk.venue_name, walk.address) == ("Bandshell", "Prospect Park, Brooklyn, NY")
    assert (walk.lat, walk.lon) == (40.6602, -73.969)
    assert walk.description == "3 miles, easy pace, all levels. Snacks after."
    comp = res.events[1]
    assert comp.start == datetime(2026, 10, 10, 0, 0, tzinfo=NY)
    assert comp.venue_name == "Example Hall" and comp.address.startswith("Example Hall, 100 Degraw")
    assert res.events[2].url == "https://feed.test/luma_sample.ics"  # no URL -> feed url


@pytest.mark.parametrize("loc, expected", [
    ("Bar X (1 Main St, New York, NY)", ("Bar X", "1 Main St, New York, NY")),
    ("Example Hall, 100 Johnson Ave, Brooklyn", ("Example Hall", "Example Hall, 100 Johnson Ave, Brooklyn")),
    ("100 Johnson Ave, Brooklyn", ("", "100 Johnson Ave, Brooklyn")),
    ("Online", ("", "Online")),
    ("", ("", "")),
])
def test_split_location(loc, expected):
    assert split_location(loc) == expected


def test_bad_date_becomes_parse_error():
    ics = b"BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:x\nSUMMARY:No date\nEND:VEVENT\nEND:VCALENDAR\n"
    res = parse_ics(ics, source="ical", feed_name="f", feed_url="https://f", tz=NY, now=NOW)
    assert res.events == [] and res.rejections[0].reason == "parse_error"


def test_category_hint_used_only_without_keyword_match(make_event):
    cfg = load_config(CONFIG)
    clf = Classifier(cfg)
    vague = make_event("Monday meetup", extra={"category_hint": "board-games"})
    specific = make_event("Pub trivia", extra={"category_hint": "board-games"})
    kept, _ = clf.classify([vague, specific])
    assert [(e.title, e.category) for e in kept] == [("Monday meetup", "board-games"), ("Pub trivia", "trivia")]


def test_registry_contract():
    reg = load_all()
    assert "ical" in reg and REGISTRY["ical"].access == "feed" and REGISTRY["ical"].tos_note


def test_luma_real_feed_link_from_description():
    res = parse_ics((FIX / "luma_calendar.ics").read_bytes(), source="ical", feed_name="luma", feed_url="https://api.lu.ma/ics/x",
                    tz=NY, now=datetime(2022, 6, 1, tzinfo=UTC), horizon_days=10)
    first = res.events[0]
    assert first.title == "Friday Morning Walk NYC"
    assert first.url == "https://luma.com/example-walk-f"
    assert not first.description.startswith("Get up-to-date") and "luma.com/example-walk-f" not in first.description
    assert (first.lat, first.lon) == (40.748538, -74.008746)
    assert first.venue_name == "Example Pier Café"


def test_cancelled_status():
    ics = (b"BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:c\nSUMMARY:Gone\nSTATUS:CANCELLED\n"
           b"DTSTART:20261001T230000Z\nEND:VEVENT\nEND:VCALENDAR\n")
    res = parse_ics(ics, source="ical", feed_name="f", feed_url="https://f", tz=NY, now=NOW)
    assert res.events == [] and res.rejections[0].reason == "cancelled"


def test_meetup_event_page_location():
    from nymetro_eventlocator.sources.ical import page_location
    html = (FIX.parent / "meetup" / "event_page.html").read_text(encoding="utf-8")
    venue, address, lat, lon, online = page_location(html, "https://www.meetup.com/example-group/events/316595431/")
    assert venue == "Example Community Hall" and address.startswith("100 Butler street, Kings County")
    assert ",," not in address and (lat, lon, online) == (None, None, False)


def test_details_fetched_once_and_cached(httpserver, store, monkeypatch):
    from werkzeug import Response as WResponse
    from nymetro_eventlocator.config import HttpSettings
    from nymetro_eventlocator.http.polite import PoliteClient
    from nymetro_eventlocator.sources.base import SourceResult
    from nymetro_eventlocator.sources.ical import IcalSource

    cfg = load_config(CONFIG)
    host = httpserver.url_for("/").split("//")[1].rstrip("/")
    httpserver.expect_request("/robots.txt").respond_with_data("", status=404)
    page = (FIX.parent / "meetup" / "event_page.html").read_text(encoding="utf-8")
    hits = []
    httpserver.expect_request("/g/events/1/").respond_with_handler(lambda r: hits.append(1) or WResponse(page, content_type="text/html"))
    src = IcalSource({"details": {"hosts": [host], "max_per_run": 5}}, cfg)
    client = PoliteClient(store, HttpSettings(), sleep=lambda s: None)
    for _ in range(2):
        out = SourceResult(events=[__import__("nymetro_eventlocator.models", fromlist=["Event"]).Event(
            "ical", "x", "Tuesday game night", NOW, httpserver.url_for("/g/events/1/"))])
        src._add_details(client, out, src.settings["details"])
        assert out.events[0].venue_name == "Example Community Hall"
    client.close()
    assert len(hits) == 1  # second run served from detail_cache


def test_luma_address_block_stripped_host_kept():
    ics = (b"BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:l1\nSUMMARY:Example Social Night\n"
           b"DTSTART:20261001T220000Z\nLOCATION:Example Loft NYC\n"
           b"DESCRIPTION:Get up-to-date information at: https://luma.com/abc\\n\\nAddress:\\nExample Loft NYC\\nNew York\\, NY"
           b"\\nUnited States\\n\\nHosted by Example Host & Example Loft NYC\nEND:VEVENT\nEND:VCALENDAR\n")
    e = parse_ics(ics, source="ical", feed_name="luma", feed_url="https://f", tz=NY, now=NOW).events[0]
    assert e.url == "https://luma.com/abc"
    assert e.description == "Hosted by Example Host & Example Loft NYC"


def test_non_luma_description_untouched():
    ics = (b"BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:m1\nSUMMARY:Game night\nDTSTART:20261001T220000Z\n"
           b"DESCRIPTION:Bring a game. Address: see group page.\nEND:VEVENT\nEND:VCALENDAR\n")
    e = parse_ics(ics, source="ical", feed_name="m", feed_url="https://f", tz=NY, now=NOW).events[0]
    assert e.description == "Bring a game. Address: see group page."
