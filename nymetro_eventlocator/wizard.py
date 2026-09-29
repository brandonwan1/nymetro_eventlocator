"""`nymetro_eventlocator init`: write a starter config.yaml, no YAML editing needed.

The starter config has no interests, so every event from the calendars the user adds is kept
(config.py's catch-all mode). Interests are added later by hand; see README "Customizing your interests".
"""

from __future__ import annotations

import copy
import shutil
from datetime import datetime
from pathlib import Path

import yaml


class WizardError(ValueError):
    pass


# Everything init writes: the NY metro area region, the map, and the optional API sources
# (Ticketmaster is enabled by default and just needs a key; Eventbrite is off until configured).
BASE: dict = {
    "region": {
        "name": "nyc",
        "timezone": "America/New_York",
        "center": [40.7128, -74.006],
        "bbox": [39.47, -75.20, 41.53, -71.77],
        "areas": [
            {"file": "nyc_boroughs.geojson", "name_key": "boroname"},
            {"file": "nyc_metro_counties.geojson", "name_key": "name"},
        ],
        "neighborhoods": [
            {"file": "nyc_neighborhoods.geojson", "name_key": "ntaname"},
            {"file": "nyc_metro_places.geojson", "name_key": "name"},
        ],
    },
    "places": {"home": None},
    "exclude_online": True,
    "web": {"map": {"style_light": "https://tiles.openfreemap.org/styles/positron",
                    "style_dark": "https://tiles.openfreemap.org/styles/dark"}},
}

TICKETMASTER = {
    "enabled": True,  # on by default; needs a key (`nymetro_eventlocator secrets set TICKETMASTER_API_KEY`) to return anything
    "api_key": "${TICKETMASTER_API_KEY}",
    "max_pages": 5,
    "days": 90,
    "window_days": 14,
    "searches": [  # circles covering the NY metro area
        {"latlong": [40.7128, -74.0060], "radius_miles": 20},
        {"latlong": [40.8500, -72.9500], "radius_miles": 30},
        {"latlong": [41.1500, -73.8500], "radius_miles": 25},
        {"latlong": [40.9500, -74.6000], "radius_miles": 30},
        {"latlong": [40.2500, -74.2500], "radius_miles": 30},
    ],
}


def build_config() -> dict:
    """A complete starter config: NY metro area, no interests (keep everything), no feeds yet."""
    return copy.deepcopy({
        **BASE,
        "groups": {},
        "categories": [],
        "exclude_keywords": [],
        "sources": {
            "ical": {"enabled": True,
                     "details": {"hosts": ["www.meetup.com"], "max_per_run": 100, "refresh_days": 7},
                     "feeds": []},
            "ticketmaster": TICKETMASTER,
            "eventbrite": {"enabled": False, "token": "${EVENTBRITE_TOKEN}", "organizers": [], "venues": []},
        },
    })


def render(cfg: dict) -> str:
    header = (
        "# nymetro_eventlocator configuration, created by `nymetro_eventlocator init` on "
        f"{datetime.now():%Y-%m-%d}.\n"
        "# No interests yet: every event from your calendars is kept in one 'All events' section.\n"
        "# Add calendars with `nymetro_eventlocator add <link>` (a Meetup group, Luma calendar or .ics link).\n"
        "# To filter, add `groups:` and `categories:`; see README \"Customizing your interests\".\n"
        "# Check edits with `nymetro_eventlocator check-config`.\n"
        "# Secrets are never written here: ${VAR} comes from `nymetro_eventlocator secrets set VAR`.\n\n"
    )
    return header + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=110, default_flow_style=None)


def write_config(path: Path, text: str, force: bool) -> Path | None:
    """Write config.yaml. An existing one is kept unless force; with force it's backed up first."""
    backup = None
    if path.exists():
        if not force:
            raise WizardError(f"{path.name} already exists. Use --force to replace it (a backup is kept).")
        backup = path.with_name(f"config.backup-{datetime.now():%Y%m%d-%H%M%S}.yaml")
        shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8")
    example = path.with_name("overrides.example.yaml")
    overrides = path.with_name("overrides.yaml")
    if example.exists() and not overrides.exists():
        shutil.copy2(example, overrides)
    return backup
