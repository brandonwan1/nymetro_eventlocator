# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) (`nymetro_eventlocator --version` shows yours).

## [Unreleased]

## [0.1.0] - unreleased (first public version)
### Added
- Collection of upcoming events from Meetup and Luma calendar feeds, any iCal feed, Ticketmaster and
  Eventbrite (official APIs, your own keys), JSON-LD event pages, confs.tech and hand-entered events.
- Keyword-based sorting into sections; with no interests set, every event is kept in one "All events" section.
- Safety filters: NY metro area boundaries, online-only, excluded words, cancelled/postponed titles, duplicates.
  Every rejected event is recorded with its reason (`rejected` command, "Didn't qualify" tab).
- A local web page with list, map (MapLibre + OpenFreeMap) and date/area filters.
- `init` (starter config for the NY metro area) and `add <link>` (add a Meetup group / Luma calendar / .ics feed
  with a preview).
- Polite HTTP gateway: robots.txt on every URL and redirect, at least 5 s between requests to a site, `Retry-After`.
- Secrets in the OS keychain or systemd-creds encrypted files, never in config or git.
- Optional daily scheduling on Linux (systemd), macOS (launchd) and Windows (Task Scheduler), kept separate from `run`.
- Lock files with exact, hash-checked dependency versions; CI on Linux, macOS and Windows.
