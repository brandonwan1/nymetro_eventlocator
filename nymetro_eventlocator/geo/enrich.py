"""Location stage: fill in coordinates, borough and neighborhood; drop events outside the region or online-only."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from nymetro_eventlocator.config import Config
from nymetro_eventlocator.geo.geocode import NON_NYC, Geocoder, venue_core
from nymetro_eventlocator.models import Event, Rejection

DATA = Path(__file__).parent / "data"
NEAR_DEG = 0.003  # ~300 m: piers and waterfront venues sit just outside the land polygons
ONLINE = re.compile(r"(?<!\w)(online|virtual|zoom|livestream|live stream|webinar)(?!\w)", re.IGNORECASE)


class Polygons:
    def __init__(self, path: Path, name_key: str):
        feats = json.loads(path.read_text(encoding="utf-8"))["features"]
        self.geoms = [shape(f["geometry"]) for f in feats]
        self.props = [f["properties"] for f in feats]
        self.names = [p[name_key] for p in self.props]
        self.tree = STRtree(self.geoms)

    def locate(self, lat: float, lon: float) -> int | None:
        pt = Point(lon, lat)
        for i in self.tree.query(pt, predicate="within"):
            return int(i)
        i = self.tree.nearest(pt)
        if i is not None and self.geoms[i].distance(pt) <= NEAR_DEG:
            return int(i)
        return None


class MultiPolygons:
    """Several boundary files searched as one (e.g. NYC boroughs + Jersey City + Long Island counties)."""

    def __init__(self, layers: list[Polygons]):
        self.layers = layers

    def locate(self, lat: float, lon: float) -> str | None:
        for layer in self.layers:  # exact containment first, in file order
            pt = Point(lon, lat)
            for i in layer.tree.query(pt, predicate="within"):
                return layer.names[int(i)]
        for layer in self.layers:  # then the ~300 m tolerance
            i = layer.locate(lat, lon)
            if i is not None:
                return layer.names[i]
        return None


@lru_cache(maxsize=8)
def load_boundaries(spec: tuple[tuple[str, str], ...]) -> MultiPolygons | None:
    if not spec:
        return None
    return MultiPolygons([Polygons(DATA / f, key) for f, key in spec])


def _spec(items: list[dict]) -> tuple[tuple[str, str], ...]:
    return tuple((b["file"], b["name_key"]) for b in items)


def in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    s, w, n, e = bbox
    return s <= lat <= n and w <= lon <= e


def looks_online(e: Event) -> bool:
    """No physical location, and the venue or title says online/virtual/zoom."""
    if e.lat is not None or (e.address and not ONLINE.search(e.address)):
        return False
    return bool(ONLINE.search(f"{e.venue_name} {e.address} {e.title}"))


def enrich(events: list[Event], cfg: Config, geocoder: Geocoder | None,
           venues: dict[str, tuple[float, float]] | None = None) -> tuple[list[Event], list[Rejection]]:
    """`venues`: manual coordinates for places the geocoders can't resolve (overrides.yaml)."""
    areas = load_boundaries(_spec(cfg.region.areas))
    hoods = load_boundaries(_spec(cfg.region.neighborhoods))
    kept: list[Event] = []
    rejected: list[Rejection] = []
    from nymetro_eventlocator.geo.tiers import place_label, tier_from_text

    for e in events:
        global_scope = (cfg.groups.get(e.group) or {}).get("scope") == "global"
        if not e.is_online and looks_online(e):
            e.is_online = True
        if e.is_online:
            if cfg.exclude_online:
                rejected.append(Rejection(e, "geo", "online_only", e.venue_name or "online"))
            else:
                kept.append(e)
            continue
        if e.lat is None and venues and e.venue_name.strip().lower() in venues:
            e.lat, e.lon = venues[e.venue_name.strip().lower()]
            e.extra["geocoded"] = "override"
        if global_scope and e.lat is None:
            # Conferences anywhere: no geocoding (the geocoders only know NYC/US addresses); tier from the text.
            e.extra["tier"] = tier_from_text(f"{e.address}, {e.venue_name}")
            e.extra["place"] = place_label(e.address) if e.address else ""
            if e.extra["tier"] != "metro":
                kept.append(e)
                continue
        if e.lat is None and geocoder is not None:
            hit = geocoder.lookup(e.address or e.venue_name)
            # Venue-name lookups go to NYC GeoSearch, so never for places known to be outside NYC
            # (it would return a same-named NYC match).
            if hit is None and e.venue_name and not NON_NYC.search(e.address):
                if e.address:
                    hit = geocoder.lookup(e.venue_name)
                core = venue_core(e.venue_name)  # "X Park Tennis Courts" -> "X Park"
                if hit is None and core:
                    hit = geocoder.lookup(core)
            if hit:
                e.lat, e.lon = hit
                e.extra["geocoded"] = True
        if e.lat is not None:
            name = areas.locate(e.lat, e.lon) if (areas is not None and in_bbox(e.lat, e.lon, cfg.region.bbox)) else None
            inside = name is not None if areas is not None else in_bbox(e.lat, e.lon, cfg.region.bbox)
            if not inside:
                if global_scope:  # allowed anywhere, just not "metro"
                    e.extra["tier"] = e.extra.get("tier") if e.extra.get("tier") in ("us", "international") else \
                        tier_from_text(f"{e.address}, {e.venue_name}") if e.address else "unknown"
                    kept.append(e)
                    continue
                why = f"{e.lat:.4f},{e.lon:.4f}" if not in_bbox(e.lat, e.lon, cfg.region.bbox) else \
                    f"outside covered areas ({e.lat:.4f},{e.lon:.4f})"
                rejected.append(Rejection(e, "geo", "out_of_region", why))
                continue
            if name:
                e.borough = name  # borough in NYC; "Jersey City", "Nassau County", ... elsewhere
                e.neighborhood = (hoods.locate(e.lat, e.lon) if hoods else None) or ""
        e.extra["tier"] = "metro" if (e.lat is not None or not global_scope) else e.extra.get("tier", "metro")
        kept.append(e)
    return kept, rejected
