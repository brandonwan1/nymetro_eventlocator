from __future__ import annotations

from nymetro_eventlocator.cli._common import polite_client

NAME = "robots"
NEEDS_CONFIG = True


def add_parser(sub) -> None:
    rb = sub.add_parser(NAME, help="show whether a URL may be fetched and the delay that applies")
    rb.add_argument("url")


def run(args, cfg) -> int:
    with polite_client(cfg) as (_, client):
        d = client.check(args.url)
    print(f"{'ALLOWED' if d.allowed else 'NOT ALLOWED'}: {d.reason}; delay between requests: {d.delay:g}s")
    return 0 if d.allowed else 1
