from __future__ import annotations

import sys
from pathlib import Path

NAME = "demo"
NEEDS_CONFIG = False  # works before any setup: no config or keys


def add_parser(sub) -> None:
    d = sub.add_parser(NAME, help="build a sample page: made-up events, or --live for real ones (no setup needed)")
    d.add_argument("--live", action="store_true",
                   help="fetch real upcoming events from two public library calendars instead of made-up ones")
    d.add_argument("--out", help="where to write the page (default: output/demo.html, or output/demo-live.html with --live)")
    d.add_argument("--open", action="store_true", help="open the page in your default browser")


def run(args, cfg) -> int:
    from nymetro_eventlocator import demo

    if args.live:
        from nymetro_eventlocator.config import HttpSettings
        from nymetro_eventlocator.db.store import Store
        from nymetro_eventlocator.http.polite import PoliteClient

        print("Fetching real events from public library calendars (robots.txt checked; about 20 seconds)...")
        client = PoliteClient(Store(":memory:"), HttpSettings())
        try:
            path, shown, rejected, report = demo.build_live(Path(args.out or "output/demo-live.html"), client)
        finally:
            client.close()
        for line in report:
            print(f"  {line}")
        if not shown and not rejected:
            print("No events could be fetched (offline?). Try the offline demo: nymetro_eventlocator demo", file=sys.stderr)
            return 1
        print(f"wrote {path}: {shown} real events in the next {demo.LIVE_DAYS} days, "
              f"{rejected} in the \"Didn't qualify\" tab")
    else:
        path, shown, rejected = demo.build(Path(args.out or "output/demo.html"))
        print(f"wrote {path}: {shown} sample events, {rejected} in the \"Didn't qualify\" tab "
              "(all made up, nothing fetched)")
    if args.open:
        import webbrowser
        webbrowser.open(path.resolve().as_uri())
    else:
        print(f"open it in a browser, or run: nymetro_eventlocator demo{' --live' if args.live else ''} --open")
    return 0
