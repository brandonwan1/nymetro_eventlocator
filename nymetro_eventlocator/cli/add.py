from __future__ import annotations

import sys
from pathlib import Path

from nymetro_eventlocator.cli._common import fmt_when, polite_client
from nymetro_eventlocator.config import load_config

NAME = "add"
NEEDS_CONFIG = True


def add_parser(sub) -> None:
    ad = sub.add_parser(NAME, help="add a Meetup group, Luma calendar or .ics link as a feed (with a preview)")
    ad.add_argument("link", help="a Meetup group page, a Luma calendar page, or an .ics / webcal link")
    ad.add_argument("--name", help="feed name (default: taken from the link)")
    ad.add_argument("--category", help="interest to use when an event's title matches no keyword")
    ad.add_argument("-y", "--yes", action="store_true", help="add without asking")


def run(args, cfg) -> int:
    from zoneinfo import ZoneInfo

    from nymetro_eventlocator import addfeed as A
    from nymetro_eventlocator.classify import Classifier, Overrides
    from nymetro_eventlocator.http.polite import FetchError
    from nymetro_eventlocator.sources.ical import parse_ics

    try:
        with polite_client(cfg) as (_, client):
            r = A.resolve(args.link, client)
            decision = client.check(r.ical_url)
            if not decision.allowed:
                raise A.AddError(f"not allowed to fetch the feed: {decision.reason}")
            resp = client.get(r.ical_url)
            if resp.status in (401, 403, 404):
                raise A.AddError(f"the feed answered HTTP {resp.status} (private or missing group?); nothing added")
            if resp.status != 200:
                raise A.AddError(f"the feed answered HTTP {resp.status}; try again later")
            res = parse_ics(resp.content, source="ical", feed_name=args.name or r.name, feed_url=r.ical_url,
                            tz=ZoneInfo(cfg.region.timezone), category_hint=args.category or "")
    except (A.AddError, FetchError, ValueError) as e:
        print(f"add: {e}", file=sys.stderr)
        return 2

    name = args.name or r.name
    print(f"Feed:  {r.ical_url}" + (f"\n       ({r.title})" if r.title else ""))
    print(f"robots.txt: {decision.reason}; {decision.delay:g}s between requests")
    kept, rejected = Classifier(cfg, Overrides.load(cfg.root / "overrides.yaml")).classify(res.events)
    print(f"Upcoming (next 90 days): {len(res.events)} events; {len(kept)} "
          + ("would be kept (no interest filtering)\n" if cfg.catch_all else "match your interests\n"))
    by_id = {e.id: e for e in kept}
    for e in sorted(res.events, key=lambda x: x.start)[:8]:
        k = by_id.get(e.id)
        reason = next((x.reason for x in rejected if x.event.id == e.id), "no matching interest")
        label = f"-> {k.group}/{k.category}" if k else f"-> ({reason.replace('_', ' ')})"
        print(f"  {fmt_when(e.start, cfg.region.timezone):22} {e.title[:55]:55} {label}")
    if not cfg.catch_all and res.events and not kept and not args.category:
        print("\nTip: none matched a keyword; use --category <interest> so this feed's events land somewhere.")
    if not cfg.sources.get("ical", {}).get("enabled"):
        print("\nNote: calendar feeds are disabled (sources.ical.enabled: false) in config.yaml.")

    if not args.yes:
        if not sys.stdin.isatty():
            print("\nNot added (run with --yes to add without asking).")
            return 0
        if input(f"\nAdd '{name}' to config.yaml? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Not added.")
            return 0
    try:
        A.add_to_config(Path(args.config).resolve(), name, r.ical_url, args.category, load_config)
    except A.AddError as e:
        print(f"add: {e}", file=sys.stderr)
        return 2
    print(f"Added '{name}'. It's included from the next `nymetro_eventlocator run` "
          "(or try `nymetro_eventlocator scrape --source ical` now).")
    return 0
