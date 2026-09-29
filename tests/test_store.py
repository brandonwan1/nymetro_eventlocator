from datetime import datetime, timedelta, UTC

from nymetro_eventlocator.db.store import MIGRATIONS, Store
from nymetro_eventlocator.models import Rejection


def test_migrates_from_scratch_and_is_idempotent(tmp_path):
    path = tmp_path / "sub" / "events.db"
    s = Store(path)
    latest = max(int(f.name.split("_")[0]) for f in MIGRATIONS.glob("*.sql"))
    assert s.conn.execute("PRAGMA user_version").fetchone()[0] == latest
    tables = {r[0] for r in s.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "rejections", "notifications", "geocode_cache", "robots_cache", "http_cache", "source_runs"} <= tables
    s.close()
    s2 = Store(path)  # reopening must not re-run migrations
    assert s2.migrate() == latest
    s2.close()


def test_upsert_is_idempotent_and_roundtrips(store, make_event):
    e = make_event("Group Hike", venue_name="Trailhead", lat=40.7, lon=-73.9, tags=["hike"], extra={"k": 1},
                   category="hiking", group="outdoors", is_online=False)
    assert store.upsert_event(e) is True
    assert store.upsert_event(e) is False
    assert store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    got = store.get_event(e.id)
    assert got == e


def test_upsert_updates_fields(store, make_event):
    e = make_event("Party")
    store.upsert_event(e)
    e.title = "Party (moved)"
    store.upsert_event(e)
    assert store.get_event(e.id).title == "Party (moved)"


def test_events_filters_and_purge(store, make_event):
    now = datetime.now(UTC)
    past = make_event("Old", start=now - timedelta(days=2), group="shows")
    soon = make_event("Soon", start=now + timedelta(days=1), group="shows")
    later = make_event("Later", start=now + timedelta(days=60), group="outdoors")
    for e in (past, soon, later):
        store.upsert_event(e)
    store.record_rejection(Rejection(make_event("Old rejected", start=now - timedelta(days=2)), "categorize", "no_category"))
    assert [e.title for e in store.events(since=now)] == ["Soon", "Later"]
    assert [e.title for e in store.events(since=now, until=now + timedelta(days=30))] == ["Soon"]
    assert [e.title for e in store.events(grp="outdoors")] == ["Later"]
    store.mark_notified([past.id], "shows")
    assert store.purge_past(now) == 1
    assert store.get_event(past.id) is None
    assert store.rejections() == []
    assert not store.was_notified(past.id, "shows")


def test_rejection_insert_then_update(store, make_event):
    e = make_event("Casino social")
    store.record_rejection(Rejection(e, "exclude", "excluded_keyword", "casino"))
    store.record_rejection(Rejection(e, "exclude", "excluded_keyword", "casino"))
    rows = store.rejections()
    assert len(rows) == 1 and rows[0]["times_seen"] == 2 and rows[0]["detail"] == "casino"
    store.record_rejection(Rejection(e, "categorize", "no_category"))
    assert {r["reason"] for r in store.rejections()} == {"excluded_keyword", "no_category"}
    assert len(store.rejections(reason="no_category")) == 1
    store.clear_rejection(e.id)
    assert store.rejections() == []


def test_notifications_per_route(store, make_event):
    e = make_event("x")
    store.upsert_event(e)
    assert not store.was_notified(e.id, "shows")
    store.mark_notified([e.id], "shows")
    store.mark_notified([e.id], "shows")
    assert store.was_notified(e.id, "shows")
    assert not store.was_notified(e.id, "outdoors")


def test_caches_and_source_runs(store):
    assert store.geocode_get("1 Main St") is None
    store.geocode_put("1 Main St", None, None)
    assert store.geocode_get("1 Main St") == (None, None)
    store.robots_put("https://a.test", 200, "User-agent: *")
    assert store.robots_get("https://a.test")["body"] == "User-agent: *"
    store.http_cache_put("https://a.test/feed", '"abc"', None, b"<rss/>")
    assert store.http_cache_get("https://a.test/feed")["etag"] == '"abc"'
    rid = store.start_source_run("skint")
    assert store.recent_parsed_counts("skint") == []
    store.finish_source_run(rid, fetched=1, parsed=12)
    assert store.recent_parsed_counts("skint") == [12]


def test_existing_database_is_backed_up_before_migrating(tmp_path, make_event):
    db = tmp_path / "events.db"
    s = Store(db)
    s.upsert_event(make_event("Kept in the backup"))
    s.conn.execute("PRAGMA user_version = 2")  # pretend it predates the newest migration
    s.conn.commit()
    s.close()
    Store(db).close()
    backups = list(tmp_path.glob("events.before-v*.db"))
    assert len(backups) == 1 and backups[0].name.startswith("events.before-v3-")
    import sqlite3
    conn = sqlite3.connect(backups[0])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("SELECT title FROM events").fetchall() == [("Kept in the backup",)]
    conn.close()


def test_new_or_current_database_makes_no_backup(tmp_path):
    Store(tmp_path / "events.db").close()   # brand-new: nothing to protect
    Store(tmp_path / "events.db").close()   # already current: nothing to migrate
    assert list(tmp_path.glob("*.before-*.db")) == []


def test_only_the_newest_backups_are_kept(tmp_path):
    import os
    s = Store(tmp_path / "events.db")
    for i in range(5):
        p = s.backup(f"before-v{i + 8}")
        os.utime(p, (1_000_000 + i, 1_000_000 + i))  # distinct times; v10+ must not sort before v9
    s.backup("before-v13")
    names = sorted(p.name.split("-")[1] for p in tmp_path.glob("events.before-v*.db"))
    assert names == ["v11", "v12", "v13"]
    s.close()
