import copy
from pathlib import Path

import pytest
import yaml

from nymetro_eventlocator.config import parse_config
from nymetro_eventlocator.geo.enrich import enrich, load_boundaries
from nymetro_eventlocator.geo.geocode import CENSUS_URL, GEOSEARCH_URL, Geocoder, clean_query, plausible
from nymetro_eventlocator.routes import assign

ROOT = Path(__file__).resolve().parent.parent
RAW = yaml.safe_load((ROOT / "tests" / "fixtures" / "test_config.yaml").read_text(encoding="utf-8"))


def cfg_with(**changes):
    raw = copy.deepcopy(RAW)
    if "routes" in changes:  # routes are optional (unused by default) and need a named output
        raw["notifiers"] = {"file": {}}
    raw.update(changes)
    return parse_config(raw, root=ROOT)


@pytest.fixture
def cfg():
    return cfg_with()


# Known points
UNION_SQ = (40.7359, -73.9911)        # Manhattan
BUSHWICK = (40.6944, -73.9213)        # Brooklyn
ASTORIA = (40.7644, -73.9235)         # Queens
YANKEE_STADIUM = (40.8296, -73.9262)  # Bronx
ST_GEORGE = (40.6437, -74.0736)       # Staten Island
# Inside the NY metro area (Census "New York-Newark-Jersey City" Metro Area, 22 counties)
HOBOKEN = (40.7440, -74.0324)         # Hudson County, NJ
YONKERS = (40.9312, -73.8987)         # Westchester County, NY
NEWARK_PRU = (40.7334, -74.1711)      # Prudential Center, Newark (Essex)
NJPAC = (40.7409, -74.1678)           # NJ Performing Arts Center, Newark
WILLIAMS_CENTER = (40.8266, -74.1066) # Williams Center, Rutherford (Bergen)
METLIFE = (40.8135, -74.0745)         # MetLife Stadium, East Rutherford (Bergen)
MONTCLAIR = (40.8176, -74.2091)       # Wellmont Theater, Montclair (Essex; a "township")
ASBURY_PARK = (40.2204, -74.0121)     # Monmouth County, NJ
JC_GROVE_ST = (40.7195, -74.0431)     # Jersey City (Hudson)
UBS_ARENA = (40.7117, -73.7253)       # UBS Arena at Belmont Park, Elmont (Nassau)
HUNTINGTON = (40.8682, -73.4257)      # Suffolk
MONTAUK = (41.0359, -71.9545)         # Suffolk, east end
# Outside it
PHILLY = (39.9526, -75.1652)          # Philadelphia
STAMFORD = (41.0534, -73.5387)        # Fairfield County, CT (Connecticut isn't in the metro area)
TRENTON = (40.2171, -74.7429)         # Mercer County, NJ
POUGHKEEPSIE = (41.7004, -73.9210)    # Dutchess County, NY


@pytest.mark.parametrize("pt, borough", [
    (UNION_SQ, "Manhattan"), (BUSHWICK, "Brooklyn"), (ASTORIA, "Queens"),
    (YANKEE_STADIUM, "Bronx"), (ST_GEORGE, "Staten Island"),
])
def test_known_points_get_right_borough(cfg, make_event, pt, borough):
    kept, rej = enrich([make_event("x", lat=pt[0], lon=pt[1])], cfg, None)
    assert rej == [] and kept[0].borough == borough
    assert kept[0].neighborhood  # every land point in NYC falls in some NTA


def test_neighborhood_names(cfg, make_event):
    kept, _ = enrich([make_event("x", lat=ASTORIA[0], lon=ASTORIA[1])], cfg, None)
    assert "Astoria" in kept[0].neighborhood


def test_pier_just_offshore_still_counts(cfg, make_event):
    pier_17 = (40.7057, -74.0020)  # Pier 17 rooftop, over the East River
    kept, rej = enrich([make_event("x", lat=pier_17[0], lon=pier_17[1])], cfg, None)
    assert rej == [] and kept[0].borough == "Manhattan"


@pytest.mark.parametrize("pt, area, hood", [
    (JC_GROVE_ST, "Hudson County", "Jersey City"),
    (HOBOKEN, "Hudson County", "Hoboken"),
    (NEWARK_PRU, "Essex County", "Newark"),
    (NJPAC, "Essex County", "Newark"),
    (MONTCLAIR, "Essex County", "Montclair"),
    (WILLIAMS_CENTER, "Bergen County", "Rutherford"),
    (METLIFE, "Bergen County", "East Rutherford"),
    (ASBURY_PARK, "Monmouth County", "Asbury Park"),
    (YONKERS, "Westchester County", "Yonkers"),
    (UBS_ARENA, "Nassau County", "Elmont"),
    (HUNTINGTON, "Suffolk County", "Huntington"),
    (MONTAUK, "Suffolk County", "Montauk"),
])
def test_ny_metro_area_is_covered(cfg, make_event, pt, area, hood):
    kept, rej = enrich([make_event("x", lat=pt[0], lon=pt[1])], cfg, None)
    assert rej == [] and (kept[0].borough, kept[0].neighborhood) == (area, hood)


def test_queens_nassau_border_prefers_exact_containment(cfg, make_event):
    queens_side = (40.7270, -73.7370)  # Queens Village, a few hundred m from the Nassau line
    kept, _ = enrich([make_event("x", lat=queens_side[0], lon=queens_side[1])], cfg, None)
    assert kept[0].borough == "Queens"


@pytest.mark.parametrize("pt", [PHILLY, STAMFORD, TRENTON, POUGHKEEPSIE])
def test_outside_nyc_is_out_of_region(cfg, make_event, pt):
    kept, rej = enrich([make_event("x", lat=pt[0], lon=pt[1])], cfg, None)
    assert kept == [] and rej[0].reason == "out_of_region"


def test_online_events(cfg, make_event):
    zoom = make_event("Book chat", venue_name="Online event (Zoom)")
    flagged = make_event("Webinar", is_online=True)
    kept, rej = enrich([zoom, flagged], cfg, None)
    assert kept == [] and [r.reason for r in rej] == ["online_only", "online_only"]
    kept, rej = enrich([make_event("z", is_online=True)], cfg_with(exclude_online=False), None)
    assert len(kept) == 1 and rej == []


def test_online_in_title_without_location(cfg, make_event):
    # Real case 2026-09-23: Meetup feed, no LOCATION, title says Online
    online = make_event("Book Club Discussion & Q&A Online")
    physical = make_event("Online marketing mixer", address="1 Main St, Brooklyn")  # has an address: not online
    kept, rej = enrich([online, physical], cfg, None)
    assert [r.reason for r in rej] == ["online_only"] and [k.title for k in kept] == ["Online marketing mixer"]


def test_unknown_location_is_kept(cfg, make_event):
    kept, rej = enrich([make_event("Somewhere")], cfg, None)
    assert len(kept) == 1 and kept[0].borough == "" and rej == []


class FakeClient:
    def __init__(self, features):
        self.features = features
        self.calls = []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        feats = self.features

        class R:
            def json(self_inner):
                return {"features": feats}
        return R()


def test_geocoder_uses_cache_and_caches_misses(store):
    hit = FakeClient([{"geometry": {"coordinates": [-73.9213, 40.6944]},
                       "properties": {"confidence": 0.9, "label": "100 Johnson Avenue, Brooklyn, NY, USA"}}])
    g = Geocoder(store, hit)
    assert g.lookup("Example Hall, 100 Johnson Ave") == (40.6944, -73.9213)
    assert g.lookup("Example Hall,  100 Johnson Ave") == (40.6944, -73.9213)  # whitespace-normalized cache hit
    assert hit.calls == [(GEOSEARCH_URL, {"text": "Example Hall, 100 Johnson Ave", "size": 1})]
    miss = FakeClient([{"geometry": {"coordinates": [0, 0]}, "properties": {"confidence": 0.2}}])
    g2 = Geocoder(store, miss)
    assert g2.lookup("Nowhere Bar") is None and g2.lookup("Nowhere Bar") is None
    assert len(miss.calls) == 1


class RoutingClient:
    """Answers GeoSearch and Census differently so we can see which one was asked."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None, **kw):
        self.calls.append(url)
        if url == CENSUS_URL:
            body = {"result": {"addressMatches": [{"coordinates": {"x": -74.0431, "y": 40.7195}}]}}
        else:
            body = {"features": [{"geometry": {"coordinates": [-73.99, 40.73]},
                                  "properties": {"confidence": 0.9, "label": "Example Hall, 100 Johnson Ave; Van Cortlandt Park"}}]}

        class R:
            def json(self_inner):
                return body
        return R()


@pytest.mark.parametrize("query, expected", [
    ("1 Main St, Jersey City, NJ 07302", CENSUS_URL),
    ("2400 Hempstead Tpke, Elmont, NY 11003", CENSUS_URL),
    ("123 Main St, Huntington, NY 11743", CENSUS_URL),
    ("1 Center St, Newark, NJ 07102", CENSUS_URL),
    ("32 Ames Ave, Rutherford, NJ 07070", CENSUS_URL),
    ("Example Hall, 100 Johnson Ave, Brooklyn", GEOSEARCH_URL),
    ("Van Cortlandt Park", GEOSEARCH_URL),
])
def test_geocoder_picks_service_by_location(store, query, expected):
    c = RoutingClient()
    assert Geocoder(store, c).lookup(query) is not None
    assert c.calls == [expected]


def test_no_venue_name_fallback_for_non_nyc_address(cfg, store, make_event):
    class CensusMiss:
        calls = []

        def get(self, url, params=None, **kw):
            self.calls.append(url)
            body = {"result": {"addressMatches": []}}

            class R:
                def json(self_inner):
                    return body
            return R()

    c = CensusMiss()
    e = make_event("x", venue_name="UBS Arena", address="2400 Hempstead Tpke, Elmont, NY 11003")
    kept, _ = enrich([e], cfg, Geocoder(store, c))
    assert c.calls == [CENSUS_URL] and kept[0].lat is None  # no GeoSearch("UBS Arena") guess


def test_geocoder_lookup_budget(store):
    c = FakeClient([])
    g = Geocoder(store, c, max_lookups=2)
    for q in ("a", "b", "c", "d"):
        g.lookup(q)
    assert len(c.calls) == 2


def test_enrich_geocodes_then_locates(cfg, store, make_event):
    c = FakeClient([{"geometry": {"coordinates": [BUSHWICK[1], BUSHWICK[0]]},
                     "properties": {"confidence": 1, "label": "Example Hall, Brooklyn, NY, USA"}}])
    kept, _ = enrich([make_event("x", venue_name="Example Hall")], cfg, Geocoder(store, c))
    assert kept[0].borough == "Brooklyn" and kept[0].extra["geocoded"]


# --- routes -------------------------------------------------------------------------------

def routed(events, cfg):
    out, rej = assign(events, cfg)
    return {k: [e.title for e in v] for k, v in out.items()}, rej


def test_no_routes_assigns_nothing_and_rejects_nothing(cfg, make_event):
    assert cfg.routes == []
    out, rej = routed([make_event("m", group="shows"), make_event("s", group="outdoors")], cfg)
    assert out == {} and rej == []


def test_borough_filter(make_event):
    cfg = cfg_with(routes=[{"id": "bk", "match": {"group": "shows", "boroughs": ["Brooklyn"]}, "to": "file:shows"}])
    bk = make_event("bk", group="shows", borough="Brooklyn")
    mn = make_event("mn", group="shows", borough="Manhattan")
    unknown = make_event("unknown", group="shows")
    out, rej = routed([bk, mn, unknown], cfg)
    assert out == {"bk": ["bk"]}
    assert sorted(r.event.title for r in rej) == ["mn", "unknown"] and {r.reason for r in rej} == {"route_filter"}


def test_near_km_filter(make_event):
    cfg = cfg_with(places={"home": list(UNION_SQ)},
                   routes=[{"id": "near", "match": {"group": "outdoors", "near": "home", "km": 3}, "to": "file:outdoors"}])
    close = make_event("close", group="outdoors", lat=40.7282, lon=-73.9942)   # ~0.9 km
    far = make_event("far", group="outdoors", lat=ASTORIA[0], lon=ASTORIA[1])  # ~6 km
    out, rej = routed([close, far], cfg)
    assert out == {"near": ["close"]} and [r.event.title for r in rej] == ["far"]


def test_areas_alias_filter(make_event):
    cfg = cfg_with(routes=[{"id": "li", "match": {"areas": ["Nassau County", "Suffolk County"]}, "to": "file:shows"}])
    out, rej = routed([make_event("ubs", borough="Nassau County"), make_event("bk", borough="Brooklyn")], cfg)
    assert out == {"li": ["ubs"]} and [r.event.title for r in rej] == ["bk"]


def test_category_list_and_multiple_routes(make_event):
    cfg = cfg_with(routes=[
        {"id": "hikes", "match": {"category": ["hiking"]}, "to": "file:outdoors"},
        {"id": "all-outdoors", "match": {"group": "outdoors"}, "to": "file:outdoors"},
    ])
    out, _ = routed([make_event("hike", group="outdoors", category="hiking")], cfg)
    assert out == {"hikes": ["hike"], "all-outdoors": ["hike"]}


def test_boundaries_load_once():
    spec = (("nyc_boroughs.geojson", "boroname"),)
    assert load_boundaries(spec) is load_boundaries(spec)
    assert load_boundaries(()) is None


@pytest.mark.parametrize("raw, cleaned", [
    # Venue/address formats seen in real feeds (2026-09-24)
    ("Bryant Park (meet at NYC Public Library front steps by the lions)", "Bryant Park"),
    ("11 W 40th St and Fifth Avenue, New York, NY, New York, NY", "11 W 40th St, New York, NY"),
    ("242 Butler street, Kings County, NY", "242 Butler street, Kings County, NY"),
    ("Example Hall, 100 Johnson Ave, Brooklyn", "Example Hall, 100 Johnson Ave, Brooklyn"),
])
def test_clean_query(raw, cleaned):
    assert clean_query(raw) == cleaned


def test_plausible_rejects_generic_only_matches():
    assert not plausible("Bryant Park", "Marine Park, Brooklyn, New York, NY, USA")
    assert plausible("Bryant Park", "Bryant Park, Manhattan, New York, NY, USA")
    assert plausible("11 W 40th St, New York, NY", "11 West 40 Street, Manhattan, New York, NY, USA")  # house number matches
    assert plausible("100 Johnson Ave", "100 Johnson Avenue, Brooklyn, NY, USA")


def test_facility_words_alone_are_not_a_match():
    # Found testing the quick start 2026-09-29: GeoSearch's top answer for "Example Valley Park Tennis Courts" was
    # a different park's tennis courts, miles away. Sharing only "tennis courts" must not count.
    assert not plausible("Example Valley Park Tennis Courts", "XYZ TENNIS COURTS, Springfield Gardens, NY, USA")
    assert plausible("Example Valley Park", "EXAMPLE VALLEY PARK, Middle Village, NY, USA")


@pytest.mark.parametrize("name, core", [
    ("Example Valley Park Tennis Courts", "Example Valley Park"),
    ("Example Park Basketball Court", "Example Park"),
    ("Example Community Hall", ""),        # nothing to strip: no second lookup
    ("Tennis Courts", ""),
])
def test_venue_core(name, core):
    from nymetro_eventlocator.geo.geocode import venue_core
    assert venue_core(name) == core


class ByQueryClient:
    """Fake GeoSearch that answers per query text, like the real one did for this venue."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, params=None, **kw):
        self.calls.append(params["text"])
        label, lat, lon = self.answers.get(params["text"], ("Nowhere", 0, 0))

        class R:
            def json(self_inner):
                return {"features": [{"geometry": {"coordinates": [lon, lat]},
                                      "properties": {"confidence": 0.8, "label": label}}]}
        return R()


def test_venue_falls_back_to_the_place_it_is_part_of(cfg, store, make_event):
    c = ByQueryClient({
        "Example Valley Park Tennis Courts": ("XYZ TENNIS COURTS, Springfield Gardens, NY, USA", 40.66903, -73.75775),
        "Example Valley Park": ("EXAMPLE VALLEY PARK, Middle Village, NY, USA", 40.72007, -73.88114),
    })
    e = make_event("Tennis", venue_name="Example Valley Park Tennis Courts",
                   address="78th Street and Example Blvd South, Queens County, NY")
    kept, _ = enrich([e], cfg, Geocoder(store, c))
    assert (kept[0].lat, kept[0].lon) == (40.72007, -73.88114) and kept[0].borough == "Queens"
    assert "Middle Village" in kept[0].neighborhood
    assert c.calls[-2:] == ["Example Valley Park Tennis Courts", "Example Valley Park"]


def test_geosearch_implausible_match_is_a_miss(store):
    c = FakeClient([{"geometry": {"coordinates": [-73.91468, 40.60959]},
                     "properties": {"confidence": 0.8, "label": "Marine Park, Brooklyn, New York, NY, USA"}}])
    assert Geocoder(store, c).lookup("Bryant Park (meet at the library steps)") is None
