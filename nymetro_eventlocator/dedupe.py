"""Merge the same real-world event listed by several sources (or already stored from an earlier run).

Two events are the same when they start within `window` of each other, their normalized titles are
similar, and their venues don't contradict each other (similar names, or coordinates within 150 m).
Already-stored events win, so something posted yesterday isn't posted again under another source's id.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

from rapidfuzz import fuzz

from nymetro_eventlocator.geo.distance import haversine_km
from nymetro_eventlocator.models import Event, Rejection

TITLE_THRESHOLD = 85
STRICT_TITLE_THRESHOLD = 90
VENUE_THRESHOLD = 80
SAME_PLACE_KM = 0.15
NOISE = {"the", "a", "an", "nyc", "new", "york", "presents", "present", "live", "tickets", "at", "w", "with", "feat", "ft"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.split(r"[^a-z0-9]+", text) if w and w not in NOISE]
    return " ".join(words)


def same_place(a: Event, b: Event) -> bool | None:
    """True/False when we can tell, None when there isn't enough information."""
    if None not in (a.lat, a.lon, b.lat, b.lon):
        return haversine_km(a.lat, a.lon, b.lat, b.lon) <= SAME_PLACE_KM
    va, vb = normalize(a.venue_name), normalize(b.venue_name)
    if va and vb:
        return fuzz.token_set_ratio(va, vb) >= VENUE_THRESHOLD
    return None


def is_duplicate(a: Event, b: Event, window: timedelta = timedelta(minutes=90)) -> bool:
    if abs(a.start - b.start) > window:
        return False
    ta, tb = normalize(a.title), normalize(b.title)
    if not ta or not tb:
        return False
    place = same_place(a, b)
    if place is False:
        return False
    if place is None:
        # No venue to confirm it: titles must match as a whole, not just share a phrase
        # ("Game Night in NYC!" vs "Brooklyn Board Game Night" are different events).
        return fuzz.token_sort_ratio(ta, tb) >= STRICT_TITLE_THRESHOLD
    return fuzz.token_set_ratio(ta, tb) >= TITLE_THRESHOLD


def richness(e: Event) -> tuple:
    """Prefer the listing with more useful detail when two new listings collide."""
    return (e.lat is not None, bool(e.venue_name), bool(e.price), len(e.description), bool(e.image))


def dedupe(new: list[Event], existing: list[Event] | None = None) -> tuple[list[Event], list[Rejection]]:
    new_ids = {e.id for e in new}
    stored = [e for e in (existing or []) if e.id not in new_ids]  # same id = an update, not a duplicate
    kept: list[Event] = []
    rejected: list[Rejection] = []

    for e in sorted(new, key=richness, reverse=True):
        twin = next((s for s in stored if is_duplicate(e, s)), None)
        if twin is not None:
            rejected.append(Rejection(e, "dedupe", "duplicate", f"already stored as {twin.id} ({twin.source})"))
            continue
        twin = next((k for k in kept if is_duplicate(e, k)), None)
        if twin is not None:
            twin.extra.setdefault("also_on", []).append({"source": e.source, "url": e.url})
            rejected.append(Rejection(e, "dedupe", "duplicate", f"merged into {twin.id} ({twin.source})"))
            continue
        kept.append(e)

    order = {e.id: i for i, e in enumerate(new)}
    kept.sort(key=lambda e: order[e.id])
    return kept, rejected
