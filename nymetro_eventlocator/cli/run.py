from __future__ import annotations

import sys

from nymetro_eventlocator.cli._common import polite_client

NAME = "run"
NEEDS_CONFIG = True


def add_parser(sub) -> None:
    sub.add_parser(NAME, help="fetch all sources once, filter, store, and update the web page, then exit")


def run(args, cfg) -> int:
    from filelock import FileLock, Timeout

    from nymetro_eventlocator.pipeline import run_once
    from nymetro_eventlocator.rejections import format_summary, parse_since

    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(cfg.db_path.parent / "run.lock"))  # works on Windows, macOS and Linux
    try:
        lock.acquire(timeout=0)
    except Timeout:
        print("another nymetro_eventlocator run is in progress; exiting", file=sys.stderr)
        return 3
    try:
        with polite_client(cfg) as (store, client):
            rep, warnings = run_once(cfg, store, client)
        for name, n in rep.per_source.items():
            print(f"{name}: {n} parsed")
        print(f"kept {len(rep.kept)} ({len(rep.new_ids)} new), swept {len(rep.swept)} stale; "
              f"{client.stats.requests} requests, {client.stats.skipped_robots} robots-skipped")
        print(format_summary(store, since=parse_since("1h")))
        for w in warnings:
            print(f"WARNING {w}")
    finally:
        lock.release()
    return 0
