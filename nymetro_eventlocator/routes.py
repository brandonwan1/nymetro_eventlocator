"""Routes (optional, undocumented for now): named subsets of the stored events, e.g. for a future output.
With no `routes:` configured (the normal case) nothing is assigned and nothing is rejected.

A route's `match` may use any of:
  group, category        str or list
  boroughs (alias: areas), neighborhoods  list (case-insensitive); areas include "Jersey City", "Nassau County", ...
  near + km              a named place from `places:` and a radius
  bbox                   [south, west, north, east]
  exclude_online         bool
  tiers                  list of metro / us / international / unknown (see nymetro_eventlocator/geo/tiers.py)
All given conditions must hold. Location conditions never match events whose location is unknown.
"""

from __future__ import annotations

from nymetro_eventlocator.config import Config, Route
from nymetro_eventlocator.geo.distance import haversine_km
from nymetro_eventlocator.geo.enrich import in_bbox
from nymetro_eventlocator.models import Event, Rejection


def _as_set(v) -> set[str]:
    return {str(x).lower() for x in (v if isinstance(v, list) else [v])}


def matches(route: Route, e: Event, cfg: Config) -> bool:
    m = route.match
    if "group" in m and e.group.lower() not in _as_set(m["group"]):
        return False
    if "category" in m and e.category.lower() not in _as_set(m["category"]):
        return False
    if m.get("exclude_online") and e.is_online:
        return False
    if "tiers" in m and e.extra.get("tier", "metro") not in _as_set(m["tiers"]):
        return False
    for key in ("boroughs", "areas"):
        if key in m and e.borough.lower() not in _as_set(m[key]):
            return False
    if "neighborhoods" in m and e.neighborhood.lower() not in _as_set(m["neighborhoods"]):
        return False
    if "near" in m:
        place = cfg.places.get(m["near"])
        if place is None or e.lat is None:
            return False
        if haversine_km(place[0], place[1], e.lat, e.lon) > float(m.get("km", 5)):
            return False
    if "bbox" in m and (e.lat is None or not in_bbox(e.lat, e.lon, tuple(m["bbox"]))):
        return False
    return True


def assign(events: list[Event], cfg: Config) -> tuple[dict[str, list[Event]], list[Rejection]]:
    out: dict[str, list[Event]] = {r.id: [] for r in cfg.routes}
    rejected: list[Rejection] = []
    if not cfg.routes:
        return out, rejected
    for e in events:
        hit = False
        for r in cfg.routes:
            if matches(r, e, cfg):
                out[r.id].append(e)
                hit = True
        if not hit:
            rejected.append(Rejection(e, "route", "route_filter", f"{e.group}/{e.category} matched no route"))
    return out, rejected
