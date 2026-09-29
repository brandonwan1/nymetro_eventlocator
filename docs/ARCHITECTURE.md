# Architecture

```
config.yaml ─► config.py ─ validates; resolves ${SECRETS} via secrets.py (OS keyring / systemd-creds / env)
                   │
 sources/*  ─► http/polite.py ─► pipeline.py
 (plugins)     the ONLY network path:        classify ─► geo/enrich (+ tiers) ─► dedupe ─► store (SQLite)
               robots.txt on every URL,          │              │                    │          │
               ≥5 s per host, backoff,           └──────────────┴────────► rejections (with reason)
               secrets masked in logs                                                │
                                                        render/page.py ─► output/index.html (list, map, "Didn't qualify")
```

## Moving parts

| Piece | File(s) | What it does |
|---|---|---|
| CLI | `nymetro_eventlocator/cli/` (one module per command) | Commands: `demo`, `init`, `add`, `run`, `scrape`, `render`, `rejected`, `secrets`, `schedule`, `robots`, `check-config` |
| Config | `nymetro_eventlocator/config.py`, `config.yaml` | Region, groups (page sections), categories (keywords/patterns), sources. Validated with clear errors. No categories = catch-all: a built-in "All events" section keeps every event that passes the safety filters. |
| Secrets | `nymetro_eventlocator/secrets.py` | Resolves `${VAR}`: systemd hand-over → chosen backend (keyring / systemd-creds / env) → environment. Values are never logged. |
| Polite gateway | `nymetro_eventlocator/http/polite.py` | robots.txt (cached 24 h, RFC 9309 rules), per-host delay (site's `Crawl-delay`, else 5 s), 429/503 `Retry-After`, conditional requests, secret masking. A test fails if any other module imports an HTTP library. |
| Sources | `nymetro_eventlocator/sources/*.py` | Plugins registered with `@register_source`. Each declares its **access rung** (`manual` / `feed` / `official_api` / `html` / `playwright`) and a ToS note. Available: `ical` (Meetup, Luma and any calendar feed), `ticketmaster`, `eventbrite`, `jsonld`, and, off in the example config, `confstech` (conferences) and `manual`. |
| Classify | `nymetro_eventlocator/classify.py` | Exclude keywords → called-off titles → category keywords (title, then description) → source/feed hint → fallback categories. `overrides.yaml` can force events in or out. |
| Location | `nymetro_eventlocator/geo/` | Coordinates from the source or a geocoder (NYC GeoSearch, US Census); boroughs and neighborhoods from bundled boundary files; `scope: global` groups get tiers (metro / us / international) instead of being rejected. |
| Dedupe | `nymetro_eventlocator/dedupe.py` | Same event across sources or runs: time window + title similarity + same venue (≤150 m). |
| Store | `nymetro_eventlocator/db/` | SQLite with numbered migrations: events, rejections, geocode / robots / HTTP / detail caches, source-run health. Past events are purged. Events that vanish from a complete fetch are swept. |
| Page | `nymetro_eventlocator/render/` | A static HTML file. Third-party text is inserted as text only. Map: MapLibre + OpenFreeMap. |
| Schedule (optional) | `nymetro_eventlocator/schedule.py` | Registers `run` with the OS scheduler: systemd (Linux), launchd (macOS) or Task Scheduler (Windows). The program itself never loops or waits. |

## One run, step by step (`pipeline.run_once`)
1. Purge past events.
2. Each enabled source fetches through the gateway. Failures are isolated, and an incomplete source is never "swept".
3. Classify, geocode and locate, then dedupe against this run and against stored events.
4. Store the kept events, and record every rejection with its reason.
5. Sweep stored events that a complete fetch no longer lists. Replacements inherit their "already seen" state.
6. Render the page. Health warnings flag sources that suddenly return nothing, and truncated queries.

## Extending
- **A new website:** audit robots.txt and the ToS, then choose the highest access rung available, add `nymetro_eventlocator/sources/<name>.py` with `@register_source`, add a config block, and write fixture tests with **synthetic** data.
- **A new category or section:** config only (`groups:` and `categories:`).
