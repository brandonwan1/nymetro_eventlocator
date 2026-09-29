from __future__ import annotations

from pathlib import Path

NAME = "demo"
NEEDS_CONFIG = False  # works before any setup: no config, network or keys


def add_parser(sub) -> None:
    d = sub.add_parser(NAME, help="build a sample page from made-up events (no setup, nothing fetched)")
    d.add_argument("--out", default="output/demo.html", help="where to write the page (default: output/demo.html)")
    d.add_argument("--open", action="store_true", help="open the page in your default browser")


def run(args, cfg) -> int:
    from nymetro_eventlocator import demo

    path, shown, rejected = demo.build(Path(args.out))
    print(f"wrote {path}: {shown} sample events, {rejected} in the \"Didn't qualify\" tab (all made up, nothing fetched)")
    if args.open:
        import webbrowser
        webbrowser.open(path.resolve().as_uri())
    else:
        print("open it in a browser, or run: nymetro_eventlocator demo --open")
    return 0
