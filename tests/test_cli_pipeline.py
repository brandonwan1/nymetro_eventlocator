"""End-to-end CLI tests for `run`, `scrape` and `render` against a local iCal feed (synthetic data, no internet)."""

import json
import re
from datetime import datetime, timedelta, UTC

import pytest

from nymetro_eventlocator.cli import main

REGION = ("region: {name: nyc, timezone: America/New_York, center: [40.71, -74.0], bbox: [39.47, -75.20, 41.53, -71.77],\n"
          "         areas: [{file: nyc_boroughs.geojson, name_key: boroname}, {file: nyc_metro_counties.geojson, name_key: name}]}\n")


def vevent(uid: str, title: str, days: int, geo: str | None = None, location: str = "") -> str:
    start = (datetime.now(UTC) + timedelta(days=days)).strftime("%Y%m%dT190000Z")
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{title}", f"DTSTART:{start}",
             f"URL:https://example.test/events/{uid}"]
    if geo:
        lines.append(f"GEO:{geo}")
    if location:
        lines.append(f"LOCATION:{location}")
    return "\r\n".join(lines + ["END:VEVENT"])


FEED = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//synthetic//EN",
    vevent("1", "Sunday group hike", 2, geo="40.6782;-73.9442", location="Example Park, Brooklyn"),
    vevent("2", "Jazz night", 3, geo="40.7061;-73.9230", location="Example Club, Brooklyn"),
    vevent("3", "Philly group hike", 4, geo="39.9526;-75.1652", location="Philadelphia, PA"),
    vevent("4", "Casino bus trip", 5, geo="40.7580;-73.9855", location="Example Plaza, Manhattan"),
    "END:VCALENDAR", ""])


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Same robots.txt checks and delay bookkeeping, but the 5 s waits don't really sleep."""
    import nymetro_eventlocator.http.polite as polite

    real = polite.PoliteClient

    class Instant(real):
        def __init__(self, *a, **kw):
            kw.setdefault("sleep", lambda s: None)
            super().__init__(*a, **kw)

    monkeypatch.setattr(polite, "PoliteClient", Instant)


@pytest.fixture
def project(tmp_path, httpserver):
    httpserver.expect_request("/robots.txt").respond_with_data("User-agent: *\nAllow: /\n")
    httpserver.expect_request("/club.ics").respond_with_data(FEED, content_type="text/calendar")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(REGION + "exclude_keywords: [casino]\n"
                   "groups: {outdoors: {label: Outdoors}}\n"
                   "categories: [{id: hiking, group: outdoors, keywords: [hike]}]\n"
                   f"sources:\n  ical:\n    enabled: true\n    feeds:\n"
                   f"      - {{ name: club, url: \"{httpserver.url_for('/club.ics')}\" }}\n", encoding="utf-8")
    return cfg


def page_data(path) -> dict:
    html = path.read_text(encoding="utf-8")
    return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1))


def test_scrape_lists_kept_and_rejected(project, capsys):
    assert main(["-c", str(project), "scrape", "--source", "ical"]) == 0
    out = capsys.readouterr().out
    assert "ical: parsed 4, kept 1 (1 new), rejected 3" in out
    assert "KEEP [outdoors/hiking]" in out and "Sunday group hike" in out
    for reason, title in (("no_category", "Jazz night"), ("out_of_region", "Philly group hike"),
                          ("excluded_keyword", "Casino bus trip")):
        assert re.search(rf"REJECT {reason}.*: {title}", out), reason


def test_scrape_unknown_source_is_an_error(project, capsys):
    assert main(["-c", str(project), "scrape", "--source", "nope"]) == 2
    assert "no source named 'nope'" in capsys.readouterr().err


def test_run_stores_renders_and_is_repeatable(project, capsys):
    assert main(["-c", str(project), "run"]) == 0
    out = capsys.readouterr().out
    assert "ical: 4 parsed" in out and "kept 1 (1 new)" in out and "Rejected 3" in out
    page = project.parent / "output" / "index.html"
    data = page_data(page)
    assert [e["title"] for e in data["events"]] == ["Sunday group hike"]
    assert {r["title"] for r in data["rejections"]} == {"Jazz night", "Philly group hike", "Casino bus trip"}
    # Second run: nothing new, and the lock from the first run was released.
    assert main(["-c", str(project), "run"]) == 0
    assert "kept 1 (0 new)" in capsys.readouterr().out


def test_run_refuses_to_overlap(project, capsys):
    from filelock import FileLock
    data = project.parent / "data"
    data.mkdir()
    with FileLock(str(data / "run.lock")):
        assert main(["-c", str(project), "run"]) == 3
    assert "another nymetro_eventlocator run is in progress" in capsys.readouterr().err


def test_render_writes_page_and_can_open_it(project, capsys, monkeypatch):
    assert main(["-c", str(project), "run"]) == 0
    capsys.readouterr()
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    assert main(["-c", str(project), "render", "--open"]) == 0
    page = project.parent / "output" / "index.html"
    assert f"wrote {page}" in capsys.readouterr().out
    assert opened == [page.resolve().as_uri()]
    assert [e["title"] for e in page_data(page)["events"]] == ["Sunday group hike"]
