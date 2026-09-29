from __future__ import annotations

import sys

from nymetro_eventlocator.cli._common import fmt_when, polite_client

NAME = "scrape"
NEEDS_CONFIG = True


def add_parser(sub) -> None:
    scrape = sub.add_parser(NAME, help="fetch one source and print what it parsed")
    scrape.add_argument("--source", required=True)


def run(args, cfg) -> int:
    from nymetro_eventlocator.pipeline import run as run_pipeline

    try:
        with polite_client(cfg) as (store, client):
            rep = run_pipeline(cfg, store, client, only=[args.source])
    except KeyError as e:
        print(e, file=sys.stderr)
        return 2
    tz = cfg.region.timezone
    print(f"\n{args.source}: parsed {rep.per_source.get(args.source, 0)}, kept {len(rep.kept)} "
          f"({len(rep.new_ids)} new), rejected {len(rep.rejected)}; HTTP requests {client.stats.requests}, "
          f"robots-skipped {client.stats.skipped_robots}")
    for err in rep.source_errors.get(args.source, []):
        print(f"  ERROR {err}")
    for e in rep.kept:
        where = ", ".join(x for x in (e.venue_name, e.neighborhood, e.borough) if x) or e.extra.get("place") \
            or e.address or "location unknown"
        print(f"  KEEP [{e.group}/{e.category}] {fmt_when(e.start, tz)}  {e.title}  @ {where}  {e.url}")
    for r in rep.rejected:
        print(f"  REJECT {r.reason}{' (' + r.detail + ')' if r.detail else ''}: {r.event.title}  {r.event.url}")
    return 0
