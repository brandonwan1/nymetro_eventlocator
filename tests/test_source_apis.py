import json
from pathlib import Path
from zoneinfo import ZoneInfo

from werkzeug import Response as WResponse

from nymetro_eventlocator.config import HttpSettings, load_config
from nymetro_eventlocator.http.polite import PoliteClient
from nymetro_eventlocator.sources import eventbrite, ticketmaster

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tests" / "fixtures" / "test_config.yaml"
NY = ZoneInfo("America/New_York")


def test_ticketmaster_parse():
    data = json.loads((FIX / "ticketmaster" / "events_page.json").read_text(encoding="utf-8"))
    res = ticketmaster.parse_page(data, NY)
    by = {e.title: e for e in res.events}
    assert set(by) == {"Example DJ Live", "Indie Band Live"}
    f = by["Example DJ Live"]
    assert f.start.isoformat() == "2026-10-10T00:00:00+00:00"
    assert (f.venue_name, f.address) == ("Example Warehouse Hall", "100 Frost St, Brooklyn, NY, 11211")
    assert (f.lat, f.lon, f.price, f.image) == (40.7196, -73.9385, "$79.5+", "https://s1.ticketm.net/big.jpg")
    assert f.tags == ["Music", "Dance/Electronic", "House"] and "Dance/Electronic" in f.description
    assert f.extra["category_hint"] == "concert" and f.extra["title_only"]
    indie = by["Indie Band Live"]
    assert indie.start.isoformat() == "2026-10-12T19:30:00-04:00" and indie.lat is None
    assert {(r.event.title, r.reason) for r in res.rejections} == {("Cancelled Gig", "cancelled"), ("No Date Yet", "parse_error")}


def test_eventbrite_parse():
    data = json.loads((FIX / "eventbrite" / "organizer_events.json").read_text(encoding="utf-8"))
    res = eventbrite.parse_page(data, "example-organizer")
    assert [e.title for e in res.events] == ["Example Artist (Live), Another Artist (Live)", "Rooftop day party"]
    a, b = res.events
    assert (a.venue_name, a.lat, a.price, a.url) == ("Example Hall", 40.7094, "$25.00", "https://www.eventbrite.com/e/111")
    assert a.start.isoformat() == "2026-10-03T02:00:00+00:00" and a.end is not None
    assert b.price == "Free" and b.lat is None and b.address.startswith("100 Johnson")
    assert [(r.event.title, r.reason) for r in res.rejections] == [("Broken", "parse_error")]


def test_missing_keys_are_reported_not_crashing(store):
    cfg = load_config(CONFIG)
    client = PoliteClient(store, HttpSettings())
    tm = ticketmaster.TicketmasterSource({"api_key": ""}, cfg).fetch(client)
    eb = eventbrite.EventbriteSource({"token": ""}, cfg).fetch(client)
    assert tm.errors == ["TICKETMASTER_API_KEY is not set"] and eb.errors == ["EVENTBRITE_TOKEN is not set"]
    assert client.stats.requests == 0
    client.close()


def test_eventbrite_sends_bearer_token_and_paginates(httpserver, store, monkeypatch):
    cfg = load_config(CONFIG)
    httpserver.expect_request("/robots.txt").respond_with_data("", status=404)
    pages = [
        {"events": [], "pagination": {"has_more_items": True, "continuation": "abc"}},
        json.loads((FIX / "eventbrite" / "organizer_events.json").read_text(encoding="utf-8")),
    ]
    seen = []

    def handler(req):
        seen.append((req.headers.get("Authorization"), req.args.get("continuation")))
        return WResponse(json.dumps(pages[len(seen) - 1]), content_type="application/json")

    httpserver.expect_request("/v3/organizers/42/events/").respond_with_handler(handler)
    monkeypatch.setattr(eventbrite, "API", httpserver.url_for("/v3"))
    sleeps = []
    client = PoliteClient(store, HttpSettings(), sleep=sleeps.append)
    res = eventbrite.EventbriteSource({"token": "TOK", "organizers": [{"name": "x", "id": "42", "category": "concert"}]}, cfg).fetch(client)
    client.close()
    assert seen == [("Bearer TOK", None), ("Bearer TOK", "abc")]
    assert len(res.events) == 2 and all(e.extra["category_hint"] == "concert" for e in res.events)
    assert sleeps and all(s <= 5 for s in sleeps)  # waited between requests to the same host


def test_ticketmaster_queries_each_search_in_date_windows(httpserver, store, monkeypatch):
    from dateutil.parser import isoparse

    cfg = load_config(CONFIG)
    httpserver.expect_request("/robots.txt").respond_with_data("", status=404)
    page = json.loads((FIX / "ticketmaster" / "events_page.json").read_text(encoding="utf-8"))
    seen = []

    def handler(req):
        seen.append((req.args["latlong"], req.args["startDateTime"], req.args["endDateTime"], req.args["apikey"]))
        return WResponse(json.dumps(page), content_type="application/json")

    httpserver.expect_request("/discovery/v2/events.json").respond_with_handler(handler)
    monkeypatch.setattr(ticketmaster, "API", httpserver.url_for("/discovery/v2/events.json"))
    client = PoliteClient(store, HttpSettings(), sleep=lambda s: None)
    settings = {"api_key": "K", "days": 90, "window_days": 30,
                "searches": [{"latlong": [40.7, -74.0], "radius_miles": 20}, {"latlong": [40.85, -72.95], "radius_miles": 30}]}
    res = ticketmaster.TicketmasterSource(settings, cfg).fetch(client)
    client.close()
    assert len(seen) == 6  # 2 searches x 3 windows, 1 page each
    for latlong in ("40.7,-74.0", "40.85,-72.95"):
        spans = sorted((isoparse(a), isoparse(b)) for ll, a, b, _ in seen if ll == latlong)
        assert all(spans[i][1] == spans[i + 1][0] for i in range(2))  # contiguous
        assert (spans[-1][1] - spans[0][0]).days == 90
    assert len(res.events) == 2  # same ids in every response are kept once


def test_missing_key_and_truncation_mark_incomplete(store):
    cfg = load_config(CONFIG)
    client = PoliteClient(store, HttpSettings())
    assert ticketmaster.TicketmasterSource({"api_key": ""}, cfg).fetch(client).incomplete == {"*"}
    assert eventbrite.EventbriteSource({"token": ""}, cfg).fetch(client).incomplete == {"*"}
    client.close()


def test_confstech_parse_and_tiers():
    from datetime import date
    from nymetro_eventlocator.geo.tiers import tier_from_text
    from nymetro_eventlocator.sources import confstech
    data = json.loads((FIX / "confstech" / "python.json").read_text(encoding="utf-8"))
    res = confstech.parse(data, "python", NY, today=date(2026, 9, 25), horizon=date(2027, 12, 31))
    titles = [e.title for e in res.events]
    assert titles == ["PyExample NYC", "ExampleConf 2027", "Nordic Python Summit", "Online Python Days"]  # past dropped
    dc = res.events[1]
    assert dc.address == "Las Vegas, NV, U.S.A." and dc.extra["multi_day"] and "CFP until 2027-05-01" in dc.description
    assert res.events[3].is_online
    assert [tier_from_text(e.address) for e in res.events[:3]] == ["metro", "us", "international"]
    assert [(r.event.title, r.reason) for r in res.rejections] == [("Broken", "parse_error")]


def test_manual_source(store):
    from nymetro_eventlocator.sources.manual import ManualSource
    cfg = load_config(CONFIG)
    client = PoliteClient(store, HttpSettings())
    res = ManualSource({"events": [
        {"title": "ExampleConf 2027", "start": "2027-08-05", "end": "2027-08-08", "url": "https://example.org/",
         "location": "Las Vegas, NV, USA", "category": "talk"},
        {"title": "No date"},
    ]}, cfg).fetch(client)
    client.close()
    assert client.stats.requests == 0
    e = res.events[0]
    assert (e.title, e.start.date().isoformat(), e.end.date().isoformat(), e.extra["category_hint"]) == \
        ("ExampleConf 2027", "2027-08-05", "2027-08-08", "talk")
    assert [r.reason for r in res.rejections] == ["parse_error"]


def test_confstech_topic_without_hint_needs_a_keyword():
    # A broad confs.tech topic lists many unrelated conferences: with no hint, only keyword matches are kept
    from nymetro_eventlocator.classify import Classifier
    cfg = load_config(CONFIG)
    from nymetro_eventlocator.sources import confstech
    from datetime import date
    data = [{"name": n, "url": "https://x.test", "startDate": "2026-11-10", "city": "Berlin", "country": "Germany"}
            for n in ("Frontend Summit Berlin", "Mobile Days London", "Berlin Python Day")]
    events = confstech.parse(data, "general", NY, today=date(2026, 9, 25), horizon=date(2027, 12, 31)).events
    kept, rej = Classifier(cfg).classify(events)
    assert [(e.title, e.category) for e in kept] == [("Berlin Python Day", "talk")]
    assert sorted(r.event.title for r in rej) == ["Frontend Summit Berlin", "Mobile Days London"] and {r.reason for r in rej} == {"no_category"}
