"""Runs the stages: sources -> classify -> geo -> dedupe -> store -> routes (optional)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC

from nymetro_eventlocator.classify import Classifier, Overrides
from nymetro_eventlocator.config import Config
from nymetro_eventlocator.db.store import Store
from nymetro_eventlocator.dedupe import dedupe, is_duplicate
from nymetro_eventlocator.geo.enrich import enrich
from nymetro_eventlocator.geo.geocode import Geocoder
from nymetro_eventlocator.http.polite import PoliteClient
from nymetro_eventlocator.models import Event, Rejection
from nymetro_eventlocator.routes import assign
from nymetro_eventlocator.sources.base import SourceResult, load_all, scope_of

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    per_source: dict[str, int] = field(default_factory=dict)
    source_errors: dict[str, list[str]] = field(default_factory=dict)
    kept: list[Event] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    routed: dict[str, list[Event]] = field(default_factory=dict)
    new_ids: set[str] = field(default_factory=set)
    swept: list[Event] = field(default_factory=list)  # stored events their source no longer lists


def enabled_sources(cfg: Config) -> dict[str, dict]:
    return {name: s for name, s in cfg.sources.items() if s.get("enabled")}


def fetch_source(name: str, cfg: Config, store: Store, client: PoliteClient) -> SourceResult:
    registry = load_all()
    if name not in registry:
        raise KeyError(f"no source named {name!r}; known: {', '.join(sorted(registry))}")
    run_id = store.start_source_run(name)
    before_req, before_skip = client.stats.requests, client.stats.skipped_robots
    try:
        res = registry[name](cfg.sources.get(name, {}), cfg).fetch(client)
    except Exception as e:  # one broken source must not stop the run
        log.exception("source %s failed", name)
        res = SourceResult(errors=[f"crashed: {e}"], incomplete={"*"})
    store.finish_source_run(
        run_id, fetched=client.stats.requests - before_req, parsed=len(res.events),
        skipped_robots=client.stats.skipped_robots - before_skip, error="; ".join(res.errors) or None,
    )
    return res


def find_stale(store: Store, name: str, res: SourceResult, since: datetime) -> list[Event]:
    """Stored future events from `name` that a complete fetch no longer returns (replaced, cancelled, deleted)."""
    if "*" in res.incomplete:
        return []
    seen = {e.id for e in res.events} | {r.event.id for r in res.rejections}
    return [e for e in store.events(since=since)
            if e.source == name and e.id not in seen and scope_of(e) not in res.incomplete]


def process(events: list[Event], cfg: Config, store: Store, client: PoliteClient | None,
            stale: list[Event] | None = None) -> RunReport:
    rep = RunReport()
    stale = stale or []
    stale_ids = {e.id for e in stale}
    now = datetime.now(UTC)
    # Already over (some sources return multi-day bundles/passes that started weeks ago): not stored at all.
    events = [e for e in events if (e.end or e.start) >= now and e.start >= now - timedelta(hours=12)]
    overrides = Overrides.load(cfg.root / "overrides.yaml")
    kept, rej = Classifier(cfg, overrides).classify(events)
    rep.rejected += rej
    kept, rej = enrich(kept, cfg, Geocoder(store, client) if client else None, overrides.venues)
    rep.rejected += rej
    existing = [e for e in store.events(since=now - timedelta(hours=12)) if e.id not in stale_ids]
    kept, rej = dedupe(kept, existing=existing)
    rep.rejected += rej
    for e in kept:
        if store.upsert_event(e):
            rep.new_ids.add(e.id)
            # A replacement for a vanished listing (e.g. Meetup swapping a placeholder id for a real one)
            # inherits its "already seen" state so it isn't treated as new again.
            twin = next((s for s in stale if is_duplicate(e, s)), None)
            if twin is not None:
                for route_id in store.notified_routes(twin.id):
                    store.mark_notified([e.id], route_id)
        store.clear_rejection(e.id)
    for s_ev in stale:
        store.delete_event(s_ev.id)
    rep.swept = stale
    for r in rep.rejected:
        store.record_rejection(r)
        store.delete_event(r.event.id)  # stored on an earlier run but no longer qualifies
    rep.kept = kept
    rep.routed, rej = assign(kept, cfg)
    for r in rej:
        store.record_rejection(r)
    rep.rejected += rej
    return rep


def run(cfg: Config, store: Store, client: PoliteClient, only: list[str] | None = None) -> RunReport:
    store.purge_past(datetime.now(UTC) - timedelta(hours=12))
    events: list[Event] = []
    source_rejections: list[Rejection] = []
    errors: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    stale: list[Event] = []
    since = datetime.now(UTC) - timedelta(hours=12)
    for name in only or enabled_sources(cfg):
        res = fetch_source(name, cfg, store, client)
        counts[name] = len(res.events)
        events += res.events
        source_rejections += res.rejections
        stale += find_stale(store, name, res, since)
        if res.errors:
            errors[name] = res.errors
    rep = process(events, cfg, store, client, stale=stale)
    for r in source_rejections:
        store.record_rejection(r)
    rep.rejected = source_rejections + rep.rejected
    rep.per_source, rep.source_errors = counts, errors
    return rep


def health_warnings(store: Store, rep: RunReport) -> list[str]:
    """Sources that usually return events but returned none this run (site changed? blocked?)."""
    out = []
    for name, n in rep.per_source.items():
        history = store.recent_parsed_counts(name, limit=6)[1:]  # skip this run
        if n == 0 and history and sum(1 for h in history if h > 0) >= 2:
            out.append(f"{name} returned 0 events (recent runs: {', '.join(map(str, history))})")
    for name, errs in rep.source_errors.items():
        out.append(f"{name}: {'; '.join(errs)[:300]}")
    return out


def run_once(cfg: Config, store: Store, client: PoliteClient) -> tuple[RunReport, list[str]]:
    """One complete pass, then return: fetch all enabled sources, process, render the page.

    Nothing here repeats or waits. Running it regularly is up to the user (`schedule install`
    registers it with the OS scheduler; see schedule.py)."""
    from nymetro_eventlocator.render.page import render

    rep = run(cfg, store, client)
    render(cfg, store)
    return rep, health_warnings(store, rep)
