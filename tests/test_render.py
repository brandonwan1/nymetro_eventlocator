import json
import re
from datetime import datetime, timedelta, UTC
from pathlib import Path

import pytest

from nymetro_eventlocator.config import load_config
from nymetro_eventlocator.models import Rejection
from nymetro_eventlocator.render.page import render

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load_config(ROOT / "tests" / "fixtures" / "test_config.yaml")


def page_data(html: str) -> dict:
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    assert m, "data block missing or broken out of"
    return json.loads(m.group(1))


def test_render_contains_events_rejections_and_filters(cfg, store, make_event, tmp_path):
    now = datetime.now(UTC)
    a = make_event("Sunday Hike", start=now + timedelta(days=2), group="outdoors", category="hiking",
                   venue_name="Gym", borough="Queens", neighborhood="Astoria", lat=40.76, lon=-73.92, price="Free")
    b = make_event("Trivia Night", start=now + timedelta(days=1), group="games", category="trivia", borough="Brooklyn")
    old = make_event("Yesterday", start=now - timedelta(days=1), group="shows", category="concert")
    for e in (a, b, old):
        store.upsert_event(e)
    store.record_rejection(Rejection(make_event("Casino night"), "exclude", "excluded_keyword", "casino"))
    out = render(cfg, store, tmp_path / "index.html", now=now)
    html = out.read_text(encoding="utf-8")
    data = page_data(html)
    assert [e["title"] for e in data["events"]] == ["Trivia Night", "Sunday Hike"]  # past dropped, sorted by start
    mat = data["events"][1]
    assert (mat["area"], mat["hood"], mat["price"], mat["group"]) == ("Queens", "Astoria", "Free", "outdoors")
    assert data["rejections"][0]["reason_label"] == "excluded keyword"
    assert list(data["groups"]) == ["outdoors", "games", "talks", "shows", "community"]   # config order = color order
    assert "2 upcoming" in html and "Rejected 1: excluded_keyword 1 (casino 1)" in html
    for control in ('id="category"', 'id="when"', 'id="area"', 'id="hood"', 'id="free"', 'id="q"', 'data-tab="map"', 'data-tab="rejected"'):
        assert control in html


def test_hostile_text_cannot_break_out(cfg, store, make_event, tmp_path):
    evil = make_event('</script><script>alert(1)</script><!--', url="javascript:alert(1)", group="shows", category="concert",
                      image="javascript:x", description="<img src=x onerror=alert(1)>")
    store.upsert_event(evil)
    html = render(cfg, store, tmp_path / "index.html").read_text(encoding="utf-8")
    assert html.count("<script>alert(1)") == 0
    data = page_data(html)
    e = data["events"][0]
    assert e["title"].startswith("</script>")  # kept as text, rendered with textContent
    assert e["url"] == "" and e["image"] == ""  # non-http links dropped
    assert "innerHTML" not in html  # the page never parses third-party text as HTML


def test_empty_database_renders(cfg, store, tmp_path):
    html = render(cfg, store, tmp_path / "index.html").read_text(encoding="utf-8")
    assert page_data(html)["events"] == [] and "0 upcoming" in html


def test_map_uses_openfreemap_not_osm_tiles(cfg, store, tmp_path):
    html = render(cfg, store, tmp_path / "index.html").read_text(encoding="utf-8")
    data = page_data(html)
    assert data["map"]["style_light"] == "https://tiles.openfreemap.org/styles/positron"
    assert data["map"]["style_dark"] == "https://tiles.openfreemap.org/styles/dark"
    assert data["map"]["js"].startswith("https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/")
    assert "tile.openstreetmap.org" not in html and "leaflet" not in html.lower()


def test_map_config_rejects_non_http_urls(cfg, store, tmp_path):
    cfg.web = {"map": {"style_light": "javascript:alert(1)", "style_dark": "https://example.test/dark"}}
    data = page_data(render(cfg, store, tmp_path / "index.html").read_text(encoding="utf-8"))
    assert data["map"]["style_light"].startswith("https://tiles.openfreemap.org")
    assert data["map"]["style_dark"] == "https://example.test/dark"


@pytest.mark.parametrize("source, url, label", [
    ("ical", "https://www.meetup.com/example-group/events/1/", "Meetup"),
    ("ical", "https://luma.com/abc", "Luma"),
    ("ticketmaster", "https://www.ticketweb.com/event/x", "Ticketmaster"),
    ("ical", "https://calendar.example.org/e/1", "Calendar"),
    ("eventbrite", "https://www.eventbrite.com/e/1", "Eventbrite"),
])
def test_source_labels(make_event, source, url, label):
    from nymetro_eventlocator.render.page import source_label
    assert source_label(make_event("x", source=source, url=url)) == label


def test_tiers_and_all_day_dates(cfg, store, make_event, tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    dc = make_event("ExampleConf 2027", source="manual", start=datetime(2027, 8, 5, 9, tzinfo=ny), end=datetime(2027, 8, 8, 18, tzinfo=ny),
                    group="talks", category="talk", address="Las Vegas Convention Center, Las Vegas, NV, USA",
                    extra={"tier": "us", "all_day": True, "multi_day": True, "place": "Las Vegas, NV, USA"})
    store.upsert_event(dc)
    html = render(cfg, store, tmp_path / "index.html").read_text(encoding="utf-8")
    e = page_data(html)["events"][0]
    assert (e["day"], e["time"], e["tier"], e["tier_label"], e["place"]) == \
        ("Thu Aug 5 – Sun Aug 8", "All day", "us", "US", "Las Vegas, NV, USA")
    assert 'id="tier"' in html and "--g0" in html


def test_date_window_options(cfg, store, tmp_path):
    html = render(cfg, store, tmp_path / "index.html").read_text(encoding="utf-8")
    block = re.search(r'<select id="when".*?</select>', html, re.S).group(0)
    assert re.findall(r'value="([^"]+)"', block) == ["all", "1", "weekend", "7", "30", "90", "365"]
    assert "Next 24 hours" in block and "Next 365 days" in block and 'id="range-note"' in html


def test_catch_all_page_has_all_events_section(store, make_event, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("region: {name: nyc, timezone: America/New_York, center: [40.71, -74.0], bbox: [39.47, -75.20, 41.53, -71.77]}\n", encoding="utf-8")
    cfg = load_config(path)
    now = datetime.now(UTC)
    store.upsert_event(make_event("Book swap", start=now + timedelta(days=1), group="all", category="all"))
    data = page_data(render(cfg, store, tmp_path / "index.html", now=now).read_text(encoding="utf-8"))
    assert data["groups"] == {"all": "All events"}
    assert [e["group"] for e in data["events"]] == ["all"]


def test_template_names_no_sections():
    """Sections come from each user's config; the page colors them by position, never by name."""
    from nymetro_eventlocator.render.page import TEMPLATES
    text = (TEMPLATES / "index.html.j2").read_text(encoding="utf-8")
    for name in ("music", "sports", "social", "career", "outdoors"):
        assert name not in text.lower(), name
