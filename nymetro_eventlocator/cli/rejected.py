from __future__ import annotations

import sys

NAME = "rejected"
NEEDS_CONFIG = True


def add_parser(sub) -> None:
    rj = sub.add_parser(NAME, help="list events that didn't qualify, and why")
    rj.add_argument("--reason", help="e.g. no_category, excluded_keyword, out_of_region, duplicate, online_only")
    rj.add_argument("--source", help="e.g. ical, ticketmaster")
    rj.add_argument("--since", help="how far back by last time seen, e.g. 7d, 12h")
    rj.add_argument("--limit", type=int, default=50, help="max rows (0 = all)")
    rj.add_argument("--summary", action="store_true", help="only print the one-line summary")


def run(args, cfg) -> int:
    from nymetro_eventlocator.db.store import Store
    from nymetro_eventlocator.rejections import format_rows, format_summary, parse_since

    try:
        since = parse_since(args.since)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    store = Store(cfg.db_path)
    print(format_summary(store, since))
    if not args.summary:
        rows = store.rejections(reason=args.reason, source=args.source, since=since)
        for line in format_rows(rows, cfg.region.timezone, args.limit or None):
            print("  " + line)
        if args.limit and len(rows) > args.limit:
            print(f"  ... {len(rows) - args.limit} more (use --limit 0 for all)")
    return 0
