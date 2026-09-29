# Source compliance audit

Audited 2026-09-22. For each candidate source this records robots.txt, the Terms of Service, the access rungs available (feed → official API → static HTML → Playwright), and the verdict.
Re-audit a source whenever its scraper breaks or once a year, whichever comes first.

Legend: ✅ allowed · ⚠️ allowed with conditions or needs your decision · ❌ excluded

## Summary

| Source | Verdict | Rung | Feeds |
|---|---|---|---|
| NYC Parks (Open Data dataset / website) | ⛔ unusable: dataset stale since 2019; website 403s automated clients | — | — |
| Ticketmaster Discovery API | ✅ (needs a free API key) | official API | concerts, sports, arts, family |
| Luma – specific calendars | ✅ | iCal feed per calendar (`api.lu.ma/ics/get?...`) | any |
| Meetup – specific groups | ✅ (iCal confirmed working 2026-09-22) | iCal feed per group (`/<group>/events/ical`) | any |
| Eventbrite – specific organizers/venues | ⚠️ (needs a free OAuth token) | official API | all |
| Shotgun | ⛔ approved but refuses automated clients (429 on every request, 2026-09-23) | — | — |
| Venue websites you name | case by case | feed → JSON-LD | any |
| NYC GeoSearch (geocoding, not an event source) | ✅ | official API (NYC Planning) | — |
| Nominatim (geocoding) | ❌ robots.txt disallows `/search` | — | — |
| US Census geocoder (addresses outside NYC) | ✅ | official API, no robots.txt → 5s | — |
| Census TIGERweb (boundary download, one-time) | ✅ | official API, robots.txt allows | — |
| Resident Advisor | ❌ | — | — |
| Dice | ❌ | — | — |
| Posh | ❌ | — | — |
| Eventbrite search pages | ❌ | — | — |
| Meetup search pages | ❌ | — | — |
| Luma discover page (`lu.ma/nyc`) | ❌ | — | — |
| AllEvents | ❌ | — | — |
| Songkick | ❌ | — | — |
| Bandsintown | ❌ | — | — |
| Edmtrain | ❌ | — | — |
| Time Out NYC | ❌ | — | — |
| SeatGeek API | ❌ by default | — | — |
| confs.tech conference data | ✅ | open-data JSON | conferences by topic |

## Details

### ⛔ NYC Parks: unusable (checked 2026-09-23)
- **Open Data dataset `fudw-fgrp`:** the newest event is dated 2019-12-28, so the dataset is no longer maintained.
- **nycgovparks.org/events/*:** robots.txt allows these pages, and the NYC.gov terms don't forbid automated access. But the site's CloudFront firewall answered `403 Request blocked` to our identified client. That's a bot block, and getting around it is off-limits.
- **Original notes:**
- The nycgovparks.org robots.txt disallows `/*json`, `/*xml` and its RSS, so the Parks **website** itself is not used.
- The same data is published officially on NYC Open Data. The Socrata API is at `data.cityofnewyork.us`, where robots.txt sets `Crawl-delay: 1` and doesn't block `/resource/`.
- Datasets: Event Listing `fudw-fgrp`, plus the Locations, Categories and Organizers tables, all joinable on `event_id`.
- NYC Open Data is published for public reuse. An app token is optional; it only raises rate limits.

### ✅ Ticketmaster Discovery API
- Official API with a free key from developer.ticketmaster.com. The default limit is 5,000 calls/day and 5/sec; one run needs about 10.
- The terms don't prohibit showing its events next to other sources.
- **Caching:** store events only "for reasonable periods in order to provide the service", so they're purged once the event is past.
- It covers most large NYC venues, including Bowery Presents/AXS and Live Nation venues.

### ✅ Luma – specific calendars (iCal)
- Luma's ToS: "you must not access the Service by any means other than our publicly supported interfaces." Scraping the `lu.ma/nyc` discover page is **not** a supported interface, so it's ❌.
- Every public Luma calendar has an **"Add iCal Subscription"** button, which is a supported interface. robots.txt for `api.lu.ma` only disallows `/insights/`.
- `nymetro_eventlocator add <luma link>` opens a calendar page once to read its id, the same as clicking "Add iCal Subscription".
- Luma feeds carry no URL field, so the event link is taken from the description ("Get up-to-date information at: …").

### ✅ Meetup – specific groups (iCal)
- robots.txt disallows keyword search (`?keywords=`), `source=`, `distance=`, `categoryId=` and the events RSS/Atom/XML, so **search-based discovery is ❌**.
- The ToS (§5.4) prohibit automated extraction "for commercial purposes"; this project is personal and non-commercial.
- The Meetup API requires a paid Meetup Pro subscription, so it's not used.
- Each group has an `/<group>/events/ical` link that Meetup itself offers, and it isn't disallowed by robots.txt. We fetch one per configured group at most once a day.
- **Confirmed 2026-09-22:** group `/<group>/events/ical/` URLs are allowed by robots.txt (no crawl-delay given, so the 5s default applies) and return `200 text/calendar` with upcoming events.
- **Event pages for location (approved by the maintainer 2026-09-23):** Meetup feeds have no LOCATION, so for each new Meetup event without one, its event page (`/<group>/events/<id>/`) is fetched once.
  - robots.txt allows these pages.
  - The venue and address are read from the page's schema.org JSON-LD.
  - Results are cached in `detail_cache` and refreshed at most weekly.
  - At most 100 page fetches per run, with the 5s delay.

### ⚠️ Eventbrite – specific organizers/venues (official API)
- The site ToS: "you agree not to scrape, crawl, or employ any automated means to extract data from the Sites." robots.txt also blocks `/rss/` and `/api/v3/destination/events/`. **Scraping search pages is ❌.**
- The official API is permitted under the API Terms:
  - 1,000 calls/hour;
  - must show the event title plus a direct link to its Eventbrite page;
  - may store **future** events only;
  - must not be "a product or service that competes with Eventbrite"; this personal, private tool isn't a product or service.
- Limitation: Eventbrite retired public event *search* in 2020, so the API can only list events by **organizer ID or venue ID**.
- **Needs:** a free Eventbrite account and a private token, plus the organizers or venues to follow.

### ✅ Shotgun (approved by the maintainer 2026-09-22)
- robots.txt: `Allow: /` for everyone.
- The GTC (May 2026) have no scraping/crawler clause. The only automation ban is on mass-sending follows and invites.
- They do say: "Aside from the normal use of the Platform, the User shall not, without Shotgun's prior written consent … use, reproduce … or exploit in any other way all or part of the Platform's components."
- It's unclear whether storing event listings counts as reproducing "Platform components". The site also returned **429** to our single ToS request, which suggests aggressive rate limiting.
- **Decision (the maintainer, 2026-09-22): include.** That clause is read as covering Shotgun's software and site components. Storing event titles, dates and links for personal use doesn't reproduce those.
- **2026-09-23 result:** `https://shotgun.live/en/cities/new-york` returned **HTTP 429** on all 3 attempts, with 5s+ backoff between them. It also returned 429 to the one-off ToS request on 2026-09-22.
  - The site is rate-limiting automated clients outright, and getting around that would break our rules, so the source is **disabled**.
  - It can be re-tested occasionally with `nymetro_eventlocator scrape --source shotgun` once the source exists.
- Conditions (if it ever becomes reachable):
  - static HTML only, preferring JSON-LD;
  - robots.txt is re-checked on every run;
  - 5s crawl delay;
  - a strict page cap;
  - on 429, honor `Retry-After`, and after two failures skip the source for the day.

### Venue websites
Each one is audited when added, using the same checklist: robots.txt, ToS, then feed → API → JSON-LD. The result is logged in this file.
Many venues' terms forbid automated access even when robots.txt allows it; when a venue sells tickets through Ticketmaster or Eventbrite, use those official APIs instead.

### ✅ US Census geocoder and TIGERweb (2026-09-23)
- **Why:** the region covers the whole NY metro area, not just NYC.
- **Boundaries:** county and place boundaries (Census CBSA 35620) came from Census TIGERweb, whose robots.txt allows the `/arcgis/rest/services/...` queries. They were downloaded once through the gateway.
- **Addresses outside NYC:** these go to `geocoding.geo.census.gov`, which has no robots.txt (so the 5s delay applies) and is a public federal service. GeoSearch only knows NYC and would mis-match them.

### ✅ NYC GeoSearch (geocoding), replacing Nominatim
- `geosearch.planninglabs.nyc` is NYC Planning's public geocoder, built on the City's official address directory. It needs no key.
- It has no robots.txt, so the 5s default delay applies. The docs state no usage restrictions.
- Results are cached permanently, so each venue or address is looked up once.
- **Nominatim was dropped:** checked 2026-09-22, its robots.txt has `Disallow: /search` (plus `/reverse` and `/lookup`) for all user agents.
- Borough and neighborhood boundaries come from NYC Open Data. They were downloaded once through the gateway and bundled in `nymetro_eventlocator/geo/data/`.

### ❌ Excluded, with reasons
- **Resident Advisor:** ToS §4.4(f): "you must not access our Website via a means we have not authorised in writing in advance, including automated devices, scripts, bots, spiders, crawlers or scrapers." robots.txt also blocks `/api/`. There's no public API.
- **Dice:** the US terms forbid using "software, devices, or other manual or automated processes to 'crawl' any page". robots.txt blocks `/api/`. There's no public API.
- **Posh:** the ToS forbid "any manual or automated software … (including … spiders, robots, scrapers, crawlers …) to 'scrape' or download data". robots.txt blocks `/api/`.
- **Eventbrite search pages / RSS:** prohibited by the ToS and robots.txt (see above). Only the official API is used.
- **Meetup search:** blocked by robots.txt, and the API is paywalled (see above).
- **Luma discover page:** not a "publicly supported interface".
- **AllEvents:** the ToS forbid you to "'crawl,' 'scrape,' or 'spider' any page … (through use of manual or automated means)".
- **Songkick:** the ToS forbid "robots, botnets, scrapers or spiders … without our express prior written consent". The API is closed to new applicants.
- **Bandsintown:** Cloudflare blocked the robots.txt request itself (403 "Sorry, you have been blocked"). Getting past bot protection is off-limits, and its API is only for artist-specific use.
- **Edmtrain:** the API terms forbid use "in an application or service that competes with our Application, such as an event discovery service that combines our events with other event sources", which is exactly what this is.
- **Time Out NYC:** the ToS §11.3 say "you may not use, transfer, copy or reproduce any part of the Content … except for the sole purpose of viewing the Content". robots.txt also blocks its RSS.
- **SeatGeek API:** the terms say "you cannot display ticket listings on behalf of other ticket sellers". Showing Ticketmaster and Eventbrite events next to it arguably conflicts with that, so it's excluded to stay safe.

## Web page map tiles (2026-09-23)
- **OpenStreetMap's tile server (`tile.openstreetmap.org`): ❌ not used.** Its tile usage policy requires a valid HTTP Referer. A page opened from `file://` sends none, so OSM blocked the tiles with a policy notice (reported by the maintainer).
- **CARTO basemaps: ❌ not used.** They now require an API key, even for personal use.
- **OpenFreeMap (`tiles.openfreemap.org`): ✅ used.**
  - It needs no registration or API key and has no usage limits.
  - Attribution ("OpenFreeMap © OpenMapTiles Data from OpenStreetMap") is added automatically by MapLibre.
  - Styles: `positron` (light) and `dark`.
- **Map library:** MapLibre GL JS 5.24.0 from jsDelivr, loaded by the viewer's browser only when the Map tab is opened. Configurable under `web.map` in config.yaml.

## Conference data (audited 2026-09-25)
- **confs.tech (✅):** github.com/tech-conferences/conference-data, MIT-licensed open data published for reuse. raw.githubusercontent.com has no robots.txt, so the 5s delay applies. Topics are configured per user (`sources.confstech.topics`).
- **Sites that only offer RSS:** not built as sources; follow them in an RSS reader.
