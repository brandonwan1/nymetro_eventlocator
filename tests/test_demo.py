import json
import re
from pathlib import Path

import yaml

from nymetro_eventlocator import demo
from nymetro_eventlocator.cli import main
from nymetro_eventlocator.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def page_data(path: Path) -> dict:
    html = path.read_text(encoding="utf-8")
    return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1))


def test_demo_page_shows_every_feature_without_network(tmp_path, monkeypatch):
    import httpx

    def no_network(*a, **kw):
        raise AssertionError("the demo must not use the network")
    monkeypatch.setattr(httpx.Client, "send", no_network)
    path, shown, rejected = demo.build(tmp_path / "demo.html")
    data = page_data(path)
    assert shown == len(data["events"]) == 15 and rejected == len(data["rejections"]) == 6
    assert set(data["groups"]) == {"outdoors", "games", "talks", "community"}
    assert {e["group"] for e in data["events"]} == set(data["groups"])            # every section has events
    assert sorted(r["reason"] for r in data["rejections"]) == sorted(
        ["cancelled", "duplicate", "excluded_keyword", "no_category", "online_only", "out_of_region"])
    assert all(e["lat"] is not None for e in data["events"])                       # all pins on the map
    assert all("example" in e["url"] for e in data["events"])                      # made-up links only


def test_demo_command_needs_no_config(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)                                                    # no config.yaml here
    assert main(["demo", "--out", "page/demo.html"]) == 0
    assert "15 sample events" in capsys.readouterr().out
    assert (tmp_path / "page" / "demo.html").exists() and not (tmp_path / "data").exists()   # no database left behind


def test_example_config_matches_the_demo():
    raw = yaml.safe_load((ROOT / "examples" / "config.filtered.yaml").read_text(encoding="utf-8"))
    for key in ("groups", "categories", "exclude_keywords"):
        assert raw[key] == demo.DEMO_INTERESTS[key], key
    cfg = load_config(ROOT / "examples" / "config.filtered.yaml")
    assert not cfg.catch_all and cfg.categories[-1].fallback


# --- demo --live (tests use a local server with made-up feeds; no internet) ---------------------------

def _libcal_like_ics(days_ahead: int = 2) -> str:
    """Shaped like a LibCal feed: the location is only a room name."""
    from datetime import UTC, datetime, timedelta
    start = (datetime.now(UTC) + timedelta(days=days_ahead)).strftime("%Y%m%dT150000Z")

    def ev(uid, title, extra=""):
        return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{title}\r\nDTSTART:{start}\r\n"
                f"LOCATION:Community Room\r\nURL:https://example.libcal.com/event/{uid}\r\n{extra}END:VEVENT\r\n")

    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + ev("1", "Knitting circle") + ev("2", "Film afternoon")
            + ev("3", "Story time", "STATUS:CANCELLED\r\n") + "END:VCALENDAR\r\n")


def _live_client(store):
    from nymetro_eventlocator.config import HttpSettings
    from nymetro_eventlocator.http.polite import PoliteClient
    return PoliteClient(store, HttpSettings(), sleep=lambda s: None)


def test_live_demo_gives_events_their_librarys_location(tmp_path, httpserver, store):
    httpserver.expect_request("/robots.txt").respond_with_data("User-agent: *\nAllow: /\n")
    httpserver.expect_request("/lib.ics").respond_with_data(_libcal_like_ics(), content_type="text/calendar")
    feeds = [{"name": "example-lib", "label": "Example Public Library", "url": httpserver.url_for("/lib.ics"),
              "address": "1 Example Ave, Hicksville, NY 11801", "coords": (40.76209, -73.52336)}]
    client = _live_client(store)
    path, shown, rejected, report = demo.build_live(tmp_path / "live.html", client, feeds)
    client.close()
    data = page_data(path)
    assert shown == 2 and report == ["Example Public Library: 2 upcoming events"]
    assert list(data["groups"]) == ["all"]                                          # keep-everything mode
    e = data["events"][0]
    assert e["venue"].startswith("Example Public Library (Community Room)") and e["area"] == "Nassau County"
    assert [r["reason"] for r in data["rejections"]] == ["cancelled"]


def test_live_demo_reports_a_failed_feed_and_keeps_going(tmp_path, httpserver, store):
    httpserver.expect_request("/robots.txt").respond_with_data("", status=404)
    httpserver.expect_request("/ok.ics").respond_with_data(_libcal_like_ics(), content_type="text/calendar")
    httpserver.expect_request("/gone.ics").respond_with_data("not here", status=404)
    base = {"address": "1 Example Ave, Hicksville, NY 11801", "coords": (40.76209, -73.52336)}
    feeds = [{"name": "gone", "label": "Gone Library", "url": httpserver.url_for("/gone.ics"), **base},
             {"name": "ok", "label": "Working Library", "url": httpserver.url_for("/ok.ics"), **base}]
    client = _live_client(store)
    _, shown, _, report = demo.build_live(tmp_path / "live.html", client, feeds)
    client.close()
    assert shown == 2 and report == ["Gone Library: HTTP 404", "Working Library: 2 upcoming events"]


def test_live_feeds_are_audited_official_feeds_inside_the_region():
    from nymetro_eventlocator.geo.enrich import in_bbox
    from nymetro_eventlocator.wizard import BASE
    bbox = tuple(BASE["region"]["bbox"])
    for f in demo.LIVE_FEEDS:
        assert f["url"].startswith("https://") and "ical_subscribe.php" in f["url"]   # the libraries' own iCal feeds
        assert in_bbox(*f["coords"], bbox), f["name"]
    compliance = (ROOT / "docs" / "sources-compliance.md").read_text(encoding="utf-8")
    assert all(f["label"] in compliance for f in demo.LIVE_FEEDS)                    # every live source is audited
