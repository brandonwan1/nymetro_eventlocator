from pathlib import Path

import pytest

from nymetro_eventlocator.config import load_config
from nymetro_eventlocator.pipeline import process
from datetime import UTC

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load_config(ROOT / "tests" / "fixtures" / "test_config.yaml")


def test_process_stores_and_is_idempotent(cfg, store, make_event):
    evs = [make_event("Sunday hike"), make_event("Trivia night"), make_event("Tax preparation seminar"), make_event("Casino")]
    rep = process(evs, cfg, store, None)
    assert sorted(e.title for e in rep.kept) == ["Sunday hike", "Trivia night"]
    assert len(rep.new_ids) == 2
    assert rep.routed == {}  # no routes configured: nothing assigned, and no route_filter rejections
    assert {r["reason"] for r in store.rejections()} == {"no_category", "excluded_keyword"}
    rep2 = process([make_event("Sunday hike"), make_event("Trivia night")], cfg, store, None)
    assert rep2.new_ids == set() and len(rep2.kept) == 2


def test_event_that_stops_qualifying_is_removed(cfg, store, make_event):
    e = make_event("Evening walk and talk", extra={"category_hint": "hiking"})
    process([e], cfg, store, None)
    assert store.get_event(e.id) is not None
    # Next run the same event (same id) is now known to be online
    again = make_event("Evening walk and talk", venue_name="Online (Zoom)", extra={"category_hint": "hiking"})
    assert again.id == e.id
    process([again], cfg, store, None)
    assert store.get_event(e.id) is None
    assert [r["reason"] for r in store.rejections()] == ["online_only"]


def test_event_that_starts_qualifying_loses_old_rejection(cfg, store, make_event):
    e = make_event("Thursday tax seminar")
    process([e], cfg, store, None)
    assert [r["reason"] for r in store.rejections()] == ["no_category"]
    e2 = make_event("Thursday tax seminar", description="then a group hike")
    process([e2], cfg, store, None)
    assert store.rejections() == [] and store.get_event(e.id).category == "hiking"


def test_events_already_over_are_not_stored(cfg, store, make_event):
    from datetime import datetime, timedelta
    now = datetime.now(UTC)
    bundle = make_event("Season bundle concert", start=now - timedelta(days=22), end=now + timedelta(days=60))
    over = make_event("Last week's concert", start=now - timedelta(days=7))
    rep = process([bundle, over], cfg, store, None)
    assert rep.kept == [] and store.events() == []


# --- stale sweep ---------------------------------------------------------------------------

from nymetro_eventlocator.pipeline import run  # noqa: E402
from nymetro_eventlocator.sources.base import REGISTRY, Source, SourceResult  # noqa: E402


class FakeSource(Source):
    name = "fake"
    access = "feed"
    tos_note = "test"
    result = SourceResult()

    def fetch(self, client):
        return FakeSource.result


@pytest.fixture
def client(store):
    from nymetro_eventlocator.config import HttpSettings
    from nymetro_eventlocator.http.polite import PoliteClient
    c = PoliteClient(store, HttpSettings())  # the fake source never touches the network
    yield c
    c.close()


@pytest.fixture
def fake(monkeypatch, cfg):
    monkeypatch.setitem(REGISTRY, "fake", FakeSource)
    monkeypatch.setattr("nymetro_eventlocator.pipeline.load_all", lambda: REGISTRY)
    cfg.sources["fake"] = {"enabled": True}
    return FakeSource


def ev(make_event, title, feed, **kw):
    return make_event(title, source="fake", source_id=kw.pop("sid", title), extra={"feed": feed, "category_hint": "hiking"}, **kw)


def test_placeholder_replaced_by_real_id_is_swept_and_inherits_notification(cfg, store, make_event, fake, client):
    # Meetup case 2026-09-23: an event's placeholder id (letters) was replaced by its real numeric id
    placeholder = ev(make_event, "Weekly Group Hike", "hikers", sid="qtrdwtyjcnbwb")
    fake.result = SourceResult(events=[placeholder])
    run(cfg, store, client, only=["fake"])
    store.mark_notified([placeholder.id], "outdoors")
    real = ev(make_event, "Weekly Group Hike", "hikers", sid="316675475")
    fake.result = SourceResult(events=[real])
    rep = run(cfg, store, client, only=["fake"])
    assert store.get_event(placeholder.id) is None and store.get_event(real.id) is not None
    assert [e.id for e in rep.swept] == [placeholder.id]
    assert store.was_notified(real.id, "outdoors")  # still counts as already seen
    assert store.rejections() == []


def test_failed_feed_or_crash_or_missing_key_never_sweeps(cfg, store, make_event, fake, client):
    a = ev(make_event, "Thursday Hike", "feed-a")
    b = ev(make_event, "Nature walk", "feed-b")
    fake.result = SourceResult(events=[a, b])
    run(cfg, store, client, only=["fake"])
    fake.result = SourceResult(events=[b], errors=["feed-a: HTTP 500"], incomplete={"feed-a"})
    run(cfg, store, client, only=["fake"])
    assert store.get_event(a.id) is not None  # its feed failed: not treated as gone
    fake.result = SourceResult(errors=["crashed"], incomplete={"*"})
    run(cfg, store, client, only=["fake"])
    assert store.get_event(a.id) is not None and store.get_event(b.id) is not None


def test_crashing_source_marks_everything_incomplete(cfg, store, make_event, fake, monkeypatch, client):
    a = ev(make_event, "Thursday Hike", "feed-a")
    fake.result = SourceResult(events=[a])
    run(cfg, store, client, only=["fake"])

    def boom(self, client):
        raise RuntimeError("parser broke")
    monkeypatch.setattr(FakeSource, "fetch", boom)
    rep = run(cfg, store, client, only=["fake"])
    assert store.get_event(a.id) is not None and "crashed" in rep.source_errors["fake"][0]


def test_really_removed_event_is_swept(cfg, store, make_event, fake, client):
    a = ev(make_event, "Thursday Hike", "feed-a")
    b = ev(make_event, "Thursday easy hike", "feed-a")
    fake.result = SourceResult(events=[a, b])
    run(cfg, store, client, only=["fake"])
    fake.result = SourceResult(events=[a])
    rep = run(cfg, store, client, only=["fake"])
    assert store.get_event(b.id) is None and [e.title for e in rep.swept] == ["Thursday easy hike"]


def test_venue_override_sets_location(store, make_event, tmp_path):
    # Self-contained: a fresh clone has no private overrides.yaml
    import shutil
    shutil.copy(ROOT / "tests" / "fixtures" / "test_config.yaml", tmp_path / "config.yaml")
    (tmp_path / "overrides.yaml").write_text('venues:\n  "Example Plaza Atrium": [40.7614, -73.9730]\n', encoding="utf-8")
    local = load_config(tmp_path / "config.yaml")
    e = make_event("Trivia in the atrium", venue_name="Example Plaza Atrium")
    rep = process([e], local, store, None)
    assert rep.kept[0].borough == "Manhattan" and rep.kept[0].extra["geocoded"] == "override"

def test_health_warning_when_productive_source_goes_quiet(cfg, store, make_event, fake, client):
    from nymetro_eventlocator.pipeline import health_warnings
    for n in (5, 6, 4):
        fake.result = SourceResult(events=[ev(make_event, f"Hike {i}", "feed-a", sid=f"{n}-{i}") for i in range(n)])
        run(cfg, store, client, only=["fake"])
    fake.result = SourceResult()
    rep = run(cfg, store, client, only=["fake"])
    warns = health_warnings(store, rep)
    assert warns and warns[0].startswith("fake returned 0 events")


def test_no_health_warning_for_normally_empty_source(cfg, store, fake, client):
    from nymetro_eventlocator.pipeline import health_warnings
    for _ in range(3):
        fake.result = SourceResult()
        rep = run(cfg, store, client, only=["fake"])
    assert health_warnings(store, rep) == []
