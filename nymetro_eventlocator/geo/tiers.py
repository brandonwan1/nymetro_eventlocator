"""Location tiers for groups that aren't limited to the region (e.g. Career conferences anywhere).

metro          inside the configured areas (the NY metro area: NYC, Long Island, Westchester/Rockland/Putnam, northern/central NJ)
us             elsewhere in the United States
international  outside the US
unknown        no usable location text
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from nymetro_eventlocator.geo.enrich import DATA

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia", "PR": "Puerto Rico",
}
US_WORDS = re.compile(r"\b(USA|U\.S\.A\.?|United States|U\.S\.)\b|,\s*(" + "|".join(US_STATES) + r")\b(?:\s+\d{5})?\s*(?:,|$)"
                      r"|\b(" + "|".join(re.escape(n) for n in US_STATES.values() if n != "Washington") + r")\b")
NYC = re.compile(r"\b(Manhattan|Brooklyn|Queens|Bronx|Staten Island|NYC|New York City|New York,\s*(NY|New York))\b"
                 # "New York, U.S.A." (confs.tech) is the city only when it starts the text; "Albany, New York, USA" is the state
                 r"|^\s*New York,\s*(U\.?S\.?A\.?|United States)\b", re.I)


@lru_cache(maxsize=1)
def _metro_places() -> re.Pattern:
    names = set()
    for f in ("nyc_metro_counties.geojson", "nyc_metro_places.geojson"):
        for feat in json.loads((DATA / f).read_text(encoding="utf-8"))["features"]:
            names.add(feat["properties"]["name"])
    names = sorted((n for n in names if len(n) > 3), key=len, reverse=True)
    # A place name counts only next to NY/NJ, so "Huntington, WV" or "Newark, DE" don't qualify.
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b[^,]*,\s*(NY|NJ|New York|New Jersey)\b", re.I)


def tier_from_text(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        return "unknown"
    if NYC.search(text) or _metro_places().search(text):
        return "metro"
    if US_WORDS.search(text):
        return "us"
    if re.search(r"[A-Za-z]", text):
        return "international"
    return "unknown"


def place_label(address: str) -> str:
    """'ExampleConf Tallinn, Tallinn, Estonia' -> 'Tallinn, Estonia' (drop a leading venue part that repeats the event)."""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    return ", ".join(parts[-3:] if len(parts) > 3 else parts[1:] if len(parts) == 3 else parts)
