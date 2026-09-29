import json
import shutil
from pathlib import Path

import pytest
import yaml

from nymetro_eventlocator import addfeed as A
from nymetro_eventlocator.__main__ import main
from nymetro_eventlocator.config import HttpSettings, load_config
from nymetro_eventlocator.http.polite import PoliteClient

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tests" / "fixtures" / "test_config.yaml"
FIX = Path(__file__).parent / "fixtures"


# --- resolving links -------------------------------------------------------------------------

@pytest.mark.parametrize("link, ical, name", [
    ("https://www.meetup.com/example-board-games/", "https://www.meetup.com/example-board-games/events/ical/", "example-board-games"),
    ("https://www.meetup.com/example-board-games/events/316625161/", "https://www.meetup.com/example-board-games/events/ical/",
     "example-board-games"),
    ("meetup.com/example-hiking-club", "https://www.meetup.com/example-hiking-club/events/ical/", "example-hiking-club"),
    ("https://www.meetup.com/de-DE/example-tech-talks/events/", "https://www.meetup.com/example-tech-talks/events/ical/",
     "example-tech-talks"),
    ("https://luma.com/calendar/cal-EXAMPLE12345", "https://api.lu.ma/ics/get?entity=calendar&id=cal-EXAMPLE12345", "cal-example12345"),
    ("https://api.lu.ma/ics/get?entity=calendar&id=cal-AAAAAAAAAA", "https://api.lu.ma/ics/get?entity=calendar&id=cal-AAAAAAAAAA", "cal-aaaaaaaaaa"),
    ("webcal://example.org/club/events.ics", "https://example.org/club/events.ics", "example-org-events-ics"),
])
def test_resolve_without_network(link, ical, name):
    r = A.resolve(link, None)
    assert (r.ical_url, r.name) == (ical, name)


@pytest.mark.parametrize("link", ["https://www.meetup.com/find/?keywords=hiking", "https://luma.com/", "https://example.org/about"])
def test_resolve_rejects_non_feeds(link):
    with pytest.raises(A.AddError):
        A.resolve(link, None)


def _luma_page(kind, cal_id="cal-OWNCALENDAR1", name="Own Calendar"):
    # Real structure (2026-09-26): the page's own calendar is initialData.data.calendar; other calendars'
    # ids appear elsewhere on the page and must NOT be picked (that mistake happened by hand once).
    data = {"props": {"pageProps": {"initialData": {"kind": kind, "data": {
        "calendar": {"api_id": cal_id, "name": name, "slug": "own"},
        "featured_items": [{"calendar": {"api_id": "cal-SOMEONEELSE9"}}] * 5}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def test_luma_page_uses_its_own_calendar(httpserver, store, monkeypatch):
    httpserver.expect_request("/robots.txt").respond_with_data("", status=404)
    httpserver.expect_request("/own").respond_with_data(_luma_page("calendar"), content_type="text/html")
    httpserver.expect_request("/one-event").respond_with_data(_luma_page("event"), content_type="text/html")
    client = PoliteClient(store, HttpSettings(), sleep=lambda s: None)
    base = httpserver.url_for("/").rstrip("/")
    cal, title = A._luma_calendar_id(f"{base}/own", client)
    assert (cal, title) == ("cal-OWNCALENDAR1", "Own Calendar")
    with pytest.raises(A.AddError, match="single Luma event"):
        A._luma_calendar_id(f"{base}/one-event", client)
    client.close()


# --- editing config.yaml, keeping comments ----------------------------------------------------

INDENTED = """# my config
sources:
  ical:
    enabled: true   # keep me
    feeds:
      - { name: a, url: "https://x.test/a.ics" }
      # a comment inside the list
      - { name: b, url: "https://x.test/b.ics", category: hiking }
  ticketmaster:
    enabled: false
"""
NOT_INDENTED = """sources:
  ical:
    enabled: true
    feeds:
    - name: a
      url: https://x.test/a.ics
    - name: b
      url: https://x.test/b.ics
  ticketmaster:
    enabled: false
"""
EMPTY = """sources:
  ical:
    enabled: true
    feeds: []            # e.g.  - { name: my-group, url: "..." }
  eventbrite: { enabled: false }
"""
MISSING = """sources:
  ical:
    enabled: true
  ticketmaster:
    enabled: false
"""


@pytest.mark.parametrize("text", [INDENTED, NOT_INDENTED, EMPTY, MISSING])
def test_insert_feed_every_layout(text):
    out = A.insert_feed(text, "new", "https://x.test/new.ics", "hiking")
    feeds = yaml.safe_load(out)["sources"]["ical"]["feeds"]
    assert feeds[-1] == {"name": "new", "url": "https://x.test/new.ics", "category": "hiking"}
    assert len(feeds) == len(yaml.safe_load(text)["sources"]["ical"].get("feeds") or []) + 1
    assert yaml.safe_load(out)["sources"]["ticketmaster" if "ticketmaster" in text else "eventbrite"]["enabled"] is False
    for line in text.splitlines():          # every original line (and comment) is still there,
        if "feeds: []" in line:              # except `feeds: []`, which must become a list; its comment stays
            assert line.split("#", 1)[1].strip() in out
            continue
        assert line in out


def test_add_to_config_validates_and_restores(tmp_path):
    cfg = tmp_path / "config.yaml"
    shutil.copy(CONFIG, cfg)
    before = cfg.read_text(encoding="utf-8")
    A.add_to_config(cfg, "new-group", "https://www.meetup.com/new-group/events/ical/", "hiking", load_config)
    assert "new-group" in cfg.read_text(encoding="utf-8") and before.splitlines()[0] in cfg.read_text(encoding="utf-8")
    loaded = load_config(cfg).sources["ical"]["feeds"]
    assert loaded[-1]["url"] == "https://www.meetup.com/new-group/events/ical/"
    after = cfg.read_text(encoding="utf-8")
    with pytest.raises(A.AddError, match="already"):
        A.add_to_config(cfg, "other", "https://www.meetup.com/new-group/events/ical/", None, load_config)
    with pytest.raises(A.AddError, match="no interest called"):
        A.add_to_config(cfg, "x", "https://x.test/x.ics", "knitting", load_config)
    assert cfg.read_text(encoding="utf-8") == after            # failed adds change nothing


# --- the command, end to end against a local server ----------------------------------------------

def test_cli_add_preview_and_add(tmp_path, httpserver, capsys, monkeypatch):
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    httpserver.expect_request("/robots.txt").respond_with_data("User-agent: *\nDisallow: /private\n")
    ics = (FIX / "ical" / "luma_sample.ics").read_bytes()
    httpserver.expect_request("/club/events.ics").respond_with_data(ics, content_type="text/calendar")
    link = httpserver.url_for("/club/events.ics")
    monkeypatch.setattr("nymetro_eventlocator.sources.ical.datetime", __import__("datetime").datetime)
    assert main(["-c", str(cfg), "add", link, "--name", "local-club", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "robots.txt: allowed" in out and "Added 'local-club'" in out
    assert any(f["name"] == "local-club" for f in load_config(cfg).sources["ical"]["feeds"])
    # robots.txt says no: nothing is fetched or added
    assert main(["-c", str(cfg), "add", httpserver.url_for("/private/cal.ics"), "--yes"]) == 2
    assert "not allowed" in capsys.readouterr().err


def test_cli_add_private_group_is_refused(tmp_path, httpserver, capsys):
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    httpserver.expect_request("/robots.txt").respond_with_data("", status=404)
    httpserver.expect_request("/g/events.ics").respond_with_data("forbidden", status=403)
    assert main(["-c", str(cfg), "add", httpserver.url_for("/g/events.ics"), "--yes"]) == 2
    assert "HTTP 403" in capsys.readouterr().err
    assert "g-events" not in cfg.read_text(encoding="utf-8")


def test_cli_add_without_yes_in_a_pipe_changes_nothing(tmp_path, httpserver, capsys):
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    before = cfg.read_text(encoding="utf-8")
    httpserver.expect_request("/robots.txt").respond_with_data("", status=404)
    httpserver.expect_request("/c.ics").respond_with_data((FIX / "ical" / "luma_sample.ics").read_bytes())
    assert main(["-c", str(cfg), "add", httpserver.url_for("/c.ics")]) == 0
    assert "Not added" in capsys.readouterr().out and cfg.read_text(encoding="utf-8") == before


def test_cli_add_preview_in_catch_all_mode(tmp_path, httpserver, capsys):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("region: {name: nyc, timezone: America/New_York, center: [40.71, -74.0], bbox: [39.47, -75.20, 41.53, -71.77]}\n"
                   "sources:\n  ical:\n    enabled: true\n    feeds: []\n", encoding="utf-8")
    httpserver.expect_request("/robots.txt").respond_with_data("", status=404)
    httpserver.expect_request("/c.ics").respond_with_data((FIX / "ical" / "luma_sample.ics").read_bytes())
    assert main(["-c", str(cfg), "add", httpserver.url_for("/c.ics"), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "no interest filtering" in out and "-> all/all" in out
    assert "no matching interest" not in out and "Tip:" not in out
    assert load_config(cfg).catch_all  # adding a feed doesn't turn filtering on
