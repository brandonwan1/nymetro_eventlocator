"""Small helpers shared by several commands."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from zoneinfo import ZoneInfo

from nymetro_eventlocator.config import Config
from nymetro_eventlocator.timefmt import fmt_day_time


def fmt_when(dt, tz: str) -> str:
    return fmt_day_time(dt.astimezone(ZoneInfo(tz)))


@contextmanager
def polite_client(cfg: Config) -> Iterator[tuple]:
    """(store, client) for commands that fetch; the client is always closed."""
    from nymetro_eventlocator.db.store import Store
    from nymetro_eventlocator.http.polite import PoliteClient

    store = Store(cfg.db_path)
    client = PoliteClient(store, cfg.http)
    try:
        yield store, client
    finally:
        client.close()
