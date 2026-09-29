"""The `nymetro_eventlocator` command line. Each command lives in its own module with:

    NAME          the subcommand
    NEEDS_CONFIG  False for commands that must work before config.yaml loads (init, secrets)
    add_parser(sub)       declare its arguments
    run(args, cfg) -> int its exit code (cfg is None when NEEDS_CONFIG is False)
"""

from __future__ import annotations

import argparse
import logging
import sys

from nymetro_eventlocator import __version__
from nymetro_eventlocator.cli import (add, check_config, init, rejected, render, robots, run, schedule, scrape,
                                      secrets)
from nymetro_eventlocator.config import ConfigError, load_config

# Order = order in `--help`.
COMMANDS = [init, add, run, scrape, render, rejected, check_config, secrets, schedule, robots]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nymetro_eventlocator",
                                description="Find events in your area, filter them by your interests, and build a web page.")
    p.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    for cmd in COMMANDS:
        cmd.add_parser(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = next(c for c in COMMANDS if c.NAME == args.command)
    if not cmd.NEEDS_CONFIG:
        return cmd.run(args, None)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines include full URLs (API keys)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    return cmd.run(args, cfg)
