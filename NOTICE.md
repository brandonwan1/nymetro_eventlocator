# Third-party data and services

Bundled data (`nymetro_eventlocator/geo/data/`):

| File | Source | Terms |
|---|---|---|
| `nyc_boroughs.geojson`, `nyc_neighborhoods.geojson` | NYC Open Data: Borough Boundaries (gthc-hcne), 2020 Neighborhood Tabulation Areas (9nt8-h7nd) | NYC Open Data Terms of Use (free public use) |
| `nyc_metro_counties.geojson`, `nyc_metro_places.geojson` | US Census Bureau TIGERweb: NY metro area (CBSA 35620) counties, NJ municipalities, NY places | US government work, public domain |

Simplified with shapely; see `nymetro_eventlocator/geo/data/SOURCE.md`.

Data fetched at run time (not bundled) comes under each provider's own terms:
- **Meetup and Luma** group/calendar iCal feeds.
- Any other iCal feed you add, under that site's terms.
- **Ticketmaster Discovery API** and **Eventbrite API:** your own keys, under their API terms.
- **confs.tech conference data** (github.com/tech-conferences/conference-data, MIT).
- **Geocoding:** NYC GeoSearch (NYC Planning) and the US Census geocoder.

The web page map uses:
- **MapLibre GL JS** (BSD-3-Clause) from jsDelivr;
- **OpenFreeMap** tiles, © OpenMapTiles, with data © OpenStreetMap contributors (ODbL). The attribution is shown on the map.

The audit trail for every source (robots.txt, terms, and the reason it was used or excluded) is in
[docs/sources-compliance.md](docs/sources-compliance.md).
