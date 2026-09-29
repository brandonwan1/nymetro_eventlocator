"""Geocode venue names/addresses. Results are cached forever, including misses.

- NYC GeoSearch (NYC Planning) for NYC places and addresses; it knows venue names but only NYC.
- US Census geocoder for addresses outside NYC (NJ, Long Island, the northern suburbs), which GeoSearch would
  otherwise mis-match to a same-named NYC street. Census needs a street address, not a venue name.
"""

from __future__ import annotations

import logging
import re

from nymetro_eventlocator.db.store import Store
from nymetro_eventlocator.http.polite import FetchError, PoliteClient

log = logging.getLogger(__name__)

GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"
MIN_CONFIDENCE = 0.6
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
# Mentions of places outside NYC: NJ, Long Island, the northern suburbs, or their ZIP codes
# (NJ 07/08xxx, Long Island 115xx-119xx, Westchester/Rockland/Putnam 105xx-109xx).
NON_NYC = re.compile(
    r"\b(NJ|New Jersey|Long Island|Nassau|Suffolk|Westchester|Rockland|Putnam|Yonkers|White Plains|New Rochelle|"
    r"Elmont|Belmont Park|Hempstead|Huntington|Montauk|Hamptons?|Garden City|Long Beach|Mineola|Hicksville|Patchogue|"
    r"Babylon|Islip|Riverhead|Jersey City|Hoboken|Newark|Rutherford)\b"
    r"|\b0[78]\d{3}\b|\b11[5-9]\d{2}\b|\b10[5-9]\d{2}\b",
    re.IGNORECASE,
)


GENERIC = {
    "the", "a", "an", "and", "of", "at", "in", "on", "by", "new", "york", "ny", "nyc", "city", "usa", "us", "united", "states",
    "st", "street", "ave", "avenue", "rd", "road", "blvd", "pl", "place", "park", "e", "w", "n", "s", "east", "west",
    "north", "south", "brooklyn", "manhattan", "queens", "bronx", "staten", "island", "county", "kings",
}


def clean_query(q: str) -> str:
    """Drop notes in parentheses, cross streets ("11 W 40th St and Fifth Avenue") and repeated city/state parts."""
    q = re.sub(r"\([^)]*\)", " ", q)
    parts, seen = [], set()
    for part in (p.strip() for p in q.split(",")):
        part = re.sub(r"\s+(?:and|&|at)\s+.*$", "", part, flags=re.IGNORECASE) if re.match(r"^\d", part) else part
        key = part.lower()
        if part and key not in seen:
            seen.add(key)
            parts.append(part)
    return " ".join(", ".join(parts).split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in GENERIC and len(w) > 1}


def plausible(query: str, label: str) -> bool:
    """A match must share a distinctive word with the query ('bryant', '40th', '599'), not just 'park' or 'ny'."""
    want = _words(query)
    return not want or bool(want & _words(label))


class Geocoder:
    def __init__(self, store: Store, client: PoliteClient | None, max_lookups: int = 60):
        self.store = store
        self.client = client
        self.remaining = max_lookups  # new network lookups allowed this run

    def lookup(self, query: str) -> tuple[float, float] | None:
        query = clean_query(query)
        if not query:
            return None
        cached = self.store.geocode_get(query)
        if cached is not None:
            return None if cached[0] is None else cached
        if self.client is None or self.remaining <= 0:
            return None
        self.remaining -= 1
        try:
            hit = self._census(query) if NON_NYC.search(query) else self._geosearch(query)
        except (FetchError, ValueError, KeyError) as e:
            log.warning("geocode failed for %r: %s", query, e)
            return None  # not cached, so it's retried next run
        self.store.geocode_put(query, *(hit or (None, None)))
        return hit

    def _geosearch(self, query: str) -> tuple[float, float] | None:
        data = self.client.get(GEOSEARCH_URL, params={"text": query, "size": 1}).json()
        feats = [f for f in data.get("features", [])
                 if f.get("properties", {}).get("confidence", 0) >= MIN_CONFIDENCE
                 and plausible(query, f.get("properties", {}).get("label", "") or f.get("properties", {}).get("name", ""))]
        if not feats:
            return None
        lon, lat = feats[0]["geometry"]["coordinates"][:2]
        return lat, lon

    def _census(self, query: str) -> tuple[float, float] | None:
        data = self.client.get(CENSUS_URL, params={"address": query, "benchmark": "Public_AR_Current", "format": "json"}).json()
        matches = data.get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        c = matches[0]["coordinates"]
        return float(c["y"]), float(c["x"])
