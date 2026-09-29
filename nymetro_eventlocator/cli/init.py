from __future__ import annotations

import sys
from pathlib import Path

from nymetro_eventlocator.config import ConfigError, load_config

NAME = "init"
NEEDS_CONFIG = False  # it creates config.yaml


def add_parser(sub) -> None:
    it = sub.add_parser(NAME, help="create a starter config.yaml (NY metro area, no interest filtering yet)")
    it.add_argument("--force", action="store_true", help="replace an existing config.yaml (a dated backup is kept)")


def run(args, cfg) -> int:
    from nymetro_eventlocator import wizard as W

    path = Path(args.config).resolve()
    try:
        backup = W.write_config(path, W.render(W.build_config()), force=args.force)
    except W.WizardError as e:
        print(f"init: {e}", file=sys.stderr)
        return 2
    try:
        load_config(path)
    except ConfigError as e:  # should never happen; keep the user's backup safe and say so
        print(f"init wrote an invalid config ({e}); please report this.", file=sys.stderr)
        return 1
    print(f"\nWrote {path.name}: NY metro area, no interest filtering (every event is kept)."
          + (f"\nYour previous config is saved as {backup.name}." if backup else ""))
    print("\nNext:")
    print("  1. nymetro_eventlocator add <meetup, luma or .ics link>   # add the calendars you follow")
    print("  2. nymetro_eventlocator run                  # fetch events (a few minutes the first time)")
    print("  3. nymetro_eventlocator render --open        # see them in your browser")
    print("Optional: filter by interest (README 'Customizing your interests'), or set "
          "TICKETMASTER_API_KEY (`nymetro_eventlocator secrets set TICKETMASTER_API_KEY`) — "
          "it's enabled by default and starts returning events as soon as a key is set.")
    return 0
