from datetime import datetime, timedelta, UTC

import pytest

from nymetro_eventlocator.dedupe import dedupe, is_duplicate, normalize
from nymetro_eventlocator.geo.distance import haversine_km

T0 = datetime(2026, 10, 3, 23, 0, tzinfo=UTC)


@pytest.fixture
def ev(make_event):
    def _ev(title, source, start=T0, **kw):
        return make_event(title, source=source, source_id=f"{source}:{title}", start=start, **kw)
    return _ev


def test_haversine_known_distance():
    # Times Square -> Empire State Building ≈ 1.07 km
    assert haversine_km(40.7580, -73.9855, 40.7484, -73.9857) == pytest.approx(1.07, abs=0.1)


def test_normalize_strips_noise_and_emoji():
    assert normalize("🎲 The Rooftop Board Game Night NYC!") == "rooftop board game night"


def test_same_event_on_two_sources_merges(ev):
    a = ev("Example Band (live set) at Example Hall", "ticketmaster", venue_name="Example Hall", price="$60")
    b = ev("EXAMPLE BAND Live Set", "listings", start=T0 + timedelta(minutes=30), venue_name="The Example Hall")
    kept, rej = dedupe([b, a])
    assert [k.source for k in kept] == ["ticketmaster"]  # richer listing kept
    assert rej[0].reason == "duplicate" and rej[0].event.source == "listings"
    assert kept[0].extra["also_on"] == [{"source": "listings", "url": b.url}]


def test_same_title_different_day_is_not_duplicate(ev):
    a = ev("Sunday Group Hike", "meetup")
    b = ev("Sunday Group Hike", "meetup-2", start=T0 + timedelta(days=7))
    kept, rej = dedupe([a, b])
    assert len(kept) == 2 and rej == []


def test_same_title_different_venue_is_not_duplicate(ev):
    a = ev("Trivia Night", "a", venue_name="Example Hall")
    b = ev("Trivia Night", "b", venue_name="Example Loft")
    assert not is_duplicate(a, b)


def test_coordinates_decide_venue(ev):
    a = ev("Chess in the Park", "a", lat=40.7265, lon=-74.0110)
    near = ev("Chess in the park", "b", lat=40.7268, lon=-74.0112)
    far = ev("Chess in the park", "c", lat=40.6782, lon=-73.9442)
    assert is_duplicate(a, near)
    assert not is_duplicate(a, far)


def test_different_titles_same_venue_and_time_not_merged(ev):
    a = ev("Board Game Night", "a", venue_name="Bar X")
    b = ev("Trivia Night", "b", venue_name="Bar X")
    assert not is_duplicate(a, b)


def test_already_stored_event_wins(ev):
    stored = ev("Rooftop Board Game Night", "group-feed")
    again = ev("Rooftop board game night NYC", "listings")
    kept, rej = dedupe([again], existing=[stored])
    assert kept == [] and "already stored" in rej[0].detail


def test_same_id_is_update_not_duplicate(ev):
    stored = ev("Rooftop Board Game Night", "group-feed")
    updated = ev("Rooftop Board Game Night", "group-feed", description="new details")
    kept, rej = dedupe([updated], existing=[stored])
    assert kept == [updated] and rej == []


def test_order_preserved_for_kept(ev):
    items = [ev(t, "s", venue_name="Same Bar") for t in ("Salsa social", "Jazz jam", "Chess club")]
    items[1].price = "$10"  # richest first internally, but output keeps input order
    kept, _ = dedupe(items)
    assert [k.title for k in kept] == ["Salsa social", "Jazz jam", "Chess club"]


def test_subset_titles_without_venue_are_not_merged(ev):
    # Real case 2026-09-23: two different Meetup groups, same Sunday 3pm slot, no venue in the feeds.
    a = ev("Board Game Night in NYC!", "ical")
    b = ev("Example Club NYC BROOKLYN 🎲 Board Game Night (Sunday) Free!", "ical")
    assert not is_duplicate(a, b)


def test_near_identical_titles_without_venue_still_merge(ev):
    a = ev("Trivia Thursdays", "a")
    b = ev("Trivia Thursdays!", "b", start=T0 + timedelta(minutes=15))
    assert is_duplicate(a, b)
