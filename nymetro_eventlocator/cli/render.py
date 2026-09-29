from __future__ import annotations

NAME = "render"
NEEDS_CONFIG = True


def add_parser(sub) -> None:
    rd = sub.add_parser(NAME, help="write the web page (output/index.html)")
    rd.add_argument("--open", action="store_true", help="open the page in your default browser")


def run(args, cfg) -> int:
    from nymetro_eventlocator.db.store import Store
    from nymetro_eventlocator.render.page import render

    out = render(cfg, Store(cfg.db_path))
    print(f"wrote {out}")
    if args.open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())  # Windows, macOS and Linux default browser
    return 0
