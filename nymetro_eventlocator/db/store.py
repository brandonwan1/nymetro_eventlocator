"""SQLite storage. Migrations in db/migrations/NNN_*.sql are applied in order, tracked by PRAGMA user_version."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, UTC
from pathlib import Path
from collections.abc import Iterable

from dateutil.parser import isoparse

from nymetro_eventlocator.models import Event, Rejection

MIGRATIONS = Path(__file__).parent / "migrations"
BACKUPS_KEPT = 3  # automatic pre-migration copies to keep

EVENT_COLUMNS = [
    "id", "source", "source_id", "title", "start", "end", "url", "description", "venue_name", "address",
    "lat", "lon", "borough", "neighborhood", "is_online", "price", "image", "category", "grp", "tags", "extra",
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    # --- schema -------------------------------------------------------------------------

    def migrate(self) -> int:
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        pending = [f for f in sorted(MIGRATIONS.glob("*.sql")) if int(f.name.split("_", 1)[0]) > current]
        if pending and current > 0:  # an existing database is about to change shape: keep a copy first
            self.backup(f"before-v{int(pending[-1].name.split('_', 1)[0])}")
        for f in pending:
            version = int(f.name.split("_", 1)[0])
            if version > current:
                with self.conn:
                    self.conn.executescript(f.read_text(encoding="utf-8"))
                    self.conn.execute(f"PRAGMA user_version = {version}")
                current = version
        return current

    def backup(self, label: str, keep: int = BACKUPS_KEPT) -> Path | None:
        """Copy the database to <name>.<label>-<timestamp>.db next to it (safe while open), keeping the newest `keep`."""
        if self.path == ":memory:":
            return None
        db = Path(self.path)
        dest = db.with_name(f"{db.stem}.{label}-{datetime.now():%Y%m%d-%H%M%S}.db")
        with sqlite3.connect(dest) as out:
            self.conn.backup(out)
        out.close()
        for old in sorted(db.parent.glob(f"{db.stem}.before-v*.db"), key=lambda f: (f.stat().st_mtime, f.name))[:-keep]:
            old.unlink()
        return dest

    # --- events -------------------------------------------------------------------------

    def upsert_event(self, e: Event) -> bool:
        """Insert or refresh an event. Returns True if it was new."""
        row = _event_row(e)
        ts = now_iso()
        existing = self.conn.execute("SELECT 1 FROM events WHERE id = ?", (row["id"],)).fetchone()
        cols = ", ".join(f'"{c}"' for c in EVENT_COLUMNS)
        marks = ", ".join("?" for _ in EVENT_COLUMNS)
        updates = ", ".join(f'"{c}" = excluded."{c}"' for c in EVENT_COLUMNS if c != "id")
        with self.conn:
            self.conn.execute(
                f"INSERT INTO events ({cols}, first_seen, last_seen) VALUES ({marks}, ?, ?) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}, last_seen = excluded.last_seen",
                [row[c] for c in EVENT_COLUMNS] + [ts, ts],
            )
        return existing is None

    def get_event(self, event_id: str) -> Event | None:
        r = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _row_event(r) if r else None

    def events(self, *, since: datetime | None = None, until: datetime | None = None, grp: str | None = None) -> list[Event]:
        q, args = "SELECT * FROM events WHERE 1=1", []
        if since:
            q += " AND start >= ?"
            args.append(since.astimezone(UTC).isoformat())
        if until:
            q += " AND start < ?"
            args.append(until.astimezone(UTC).isoformat())
        if grp:
            q += " AND grp = ?"
            args.append(grp)
        return [_row_event(r) for r in self.conn.execute(q + " ORDER BY start", args)]

    def delete_event(self, event_id: str) -> bool:
        """Remove an event that no longer qualifies (rules changed, or now known to be online, etc.)."""
        with self.conn:
            n = self.conn.execute("DELETE FROM events WHERE id = ?", (event_id,)).rowcount
            self.conn.execute("DELETE FROM notifications WHERE event_id = ?", (event_id,))
        return n > 0

    def purge_past(self, before: datetime) -> int:
        """Delete events (and their rejections) that started before `before`. Some API terms forbid keeping past events."""
        cutoff = before.astimezone(UTC).isoformat()
        with self.conn:
            n = self.conn.execute("DELETE FROM events WHERE start < ?", (cutoff,)).rowcount
            self.conn.execute("DELETE FROM rejections WHERE start IS NOT NULL AND start < ?", (cutoff,))
            self.conn.execute("DELETE FROM notifications WHERE event_id NOT IN (SELECT id FROM events)")
        return n

    # --- rejections ---------------------------------------------------------------------

    def record_rejection(self, rj: Rejection) -> None:
        e, ts = rj.event, now_iso()
        with self.conn:
            self.conn.execute(
                "INSERT INTO rejections (event_id, reason, stage, detail, source, title, url, start, venue_name, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(event_id, reason) DO UPDATE SET detail = excluded.detail, stage = excluded.stage, "
                "title = excluded.title, last_seen = excluded.last_seen, times_seen = times_seen + 1",
                (e.id, rj.reason, rj.stage, rj.detail, e.source, e.title, e.url, _iso(e.start), e.venue_name, ts, ts),
            )

    def clear_rejection(self, event_id: str) -> None:
        """An event that now qualifies (rules changed) shouldn't keep showing as rejected."""
        with self.conn:
            self.conn.execute("DELETE FROM rejections WHERE event_id = ?", (event_id,))

    def rejections(self, *, reason: str | None = None, source: str | None = None, since: datetime | None = None) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM rejections WHERE 1=1", []
        if reason:
            q += " AND reason = ?"
            args.append(reason)
        if source:
            q += " AND source = ?"
            args.append(source)
        if since:
            q += " AND last_seen >= ?"
            args.append(since.astimezone(UTC).isoformat(timespec="seconds"))
        return list(self.conn.execute(q + " ORDER BY last_seen DESC, reason", args))

    # --- notifications ------------------------------------------------------------------

    def was_notified(self, event_id: str, route_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM notifications WHERE event_id = ? AND route_id = ?", (event_id, route_id)
        ).fetchone() is not None

    def notified_routes(self, event_id: str) -> list[str]:
        return [r["route_id"] for r in self.conn.execute("SELECT route_id FROM notifications WHERE event_id = ?", (event_id,))]

    def mark_notified(self, event_ids: Iterable[str], route_id: str) -> None:
        ts = now_iso()
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO notifications (event_id, route_id, sent_at) VALUES (?, ?, ?)",
                [(eid, route_id, ts) for eid in event_ids],
            )

    # --- caches -------------------------------------------------------------------------

    def geocode_get(self, query: str) -> tuple[float | None, float | None] | None:
        r = self.conn.execute("SELECT lat, lon FROM geocode_cache WHERE query = ?", (query,)).fetchone()
        return (r["lat"], r["lon"]) if r else None

    def geocode_put(self, query: str, lat: float | None, lon: float | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO geocode_cache (query, lat, lon, fetched_at) VALUES (?, ?, ?, ?)",
                (query, lat, lon, now_iso()),
            )

    def detail_get(self, url: str, max_age_days: float) -> sqlite3.Row | None:
        r = self.conn.execute("SELECT * FROM detail_cache WHERE url = ?", (url,)).fetchone()
        if r is None or datetime.now(UTC) - datetime.fromisoformat(r["fetched_at"]) > timedelta(days=max_age_days):
            return None
        return r

    def detail_put(self, url: str, venue_name: str, address: str, lat: float | None, lon: float | None, is_online: bool) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO detail_cache (url, venue_name, address, lat, lon, is_online, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (url, venue_name, address, lat, lon, int(is_online), now_iso()),
            )

    def robots_get(self, origin: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM robots_cache WHERE origin = ?", (origin,)).fetchone()

    def robots_put(self, origin: str, status: int, body: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO robots_cache (origin, status, body, fetched_at) VALUES (?, ?, ?, ?)",
                (origin, status, body, now_iso()),
            )

    def http_cache_get(self, url: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM http_cache WHERE url = ?", (url,)).fetchone()

    def http_cache_put(self, url: str, etag: str | None, last_modified: str | None, body: bytes) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO http_cache (url, etag, last_modified, body, fetched_at) VALUES (?, ?, ?, ?, ?)",
                (url, etag, last_modified, body, now_iso()),
            )

    # --- source runs --------------------------------------------------------------------

    def start_source_run(self, source: str) -> int:
        with self.conn:
            return self.conn.execute(
                "INSERT INTO source_runs (source, started_at) VALUES (?, ?)", (source, now_iso())
            ).lastrowid

    def finish_source_run(self, run_id: int, *, fetched: int = 0, parsed: int = 0, skipped_robots: int = 0, error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE source_runs SET finished_at = ?, fetched = ?, parsed = ?, skipped_robots = ?, error = ? WHERE id = ?",
                (now_iso(), fetched, parsed, skipped_robots, error, run_id),
            )

    def recent_parsed_counts(self, source: str, limit: int = 5) -> list[int]:
        """Parsed counts of the last completed runs, newest first (excluding any run still in progress)."""
        rows = self.conn.execute(
            "SELECT parsed FROM source_runs WHERE source = ? AND finished_at IS NOT NULL ORDER BY id DESC LIMIT ?",
            (source, limit),
        )
        return [r["parsed"] for r in rows]


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def _event_row(e: Event) -> dict:
    d = asdict(e)
    d["id"] = e.id
    d["start"] = _iso(e.start)
    d["end"] = _iso(e.end)
    d["grp"] = d.pop("group")
    d["is_online"] = int(e.is_online)
    d["tags"] = json.dumps(e.tags)
    d["extra"] = json.dumps(e.extra, default=str)
    return d


def _row_event(r: sqlite3.Row) -> Event:
    return Event(
        source=r["source"], source_id=r["source_id"], title=r["title"], start=isoparse(r["start"]), url=r["url"],
        end=isoparse(r["end"]) if r["end"] else None, description=r["description"], venue_name=r["venue_name"],
        address=r["address"], lat=r["lat"], lon=r["lon"], borough=r["borough"], neighborhood=r["neighborhood"],
        is_online=bool(r["is_online"]), price=r["price"], image=r["image"], category=r["category"], group=r["grp"],
        tags=json.loads(r["tags"]), extra=json.loads(r["extra"]),
    )
