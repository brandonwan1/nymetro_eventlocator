from pathlib import Path

import pytest

from nymetro_eventlocator.config import load_config
from nymetro_eventlocator.geo.enrich import enrich
from nymetro_eventlocator.geo.tiers import place_label, tier_from_text
from nymetro_eventlocator.routes import matches

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tests" / "fixtures" / "test_config.yaml"


@pytest.fixture(scope="module")
def cfg():
    """Sections are local by default; these tests exercise the optional `scope: global`."""
    import yaml
    from nymetro_eventlocator.config import parse_config
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    raw["groups"]["talks"]["scope"] = "global"
    return parse_config(raw, root=ROOT)


def test_sections_are_local_by_default(make_event):
    local = load_config(CONFIG)
    far = make_event("ExampleConf Orlando", group="talks", category="talk", lat=28.5383, lon=-81.3792)
    kept, rej = enrich([far], local, None)
    assert kept == [] and rej[0].reason == "out_of_region"


@pytest.mark.parametrize("text, tier", [
    # Location formats seen in conference feeds: "<event name>, <city>, <region>, <country>"
    ("ExampleConfTallinn, Tallinn, Estonia", "international"),
    ("ExampleConfABQ, Albuquerque, NM, USA", "us"),
    ("ExampleConfCT, (multiple locations), CT, USA", "us"),
    ("ExampleConfVI, Victoria, BC, Canada", "international"),
    ("Las Vegas Convention Center, Las Vegas, Nevada", "us"),
    ("St. John's University, Queens, NY", "metro"),
    ("New York, U.S.A.", "metro"),                  # confs.tech format
    ("Albany, New York, USA", "us"),
    ("NJIT, Newark, NJ 07102", "metro"),
    ("Hofstra University, Hempstead, NY", "metro"),
    ("Some hall, Newark, DE", "us"),            # Newark, Delaware is not metro
    ("Marshall University, Huntington, WV", "us"),  # Huntington, WV is not Huntington, NY
    ("", "unknown"),
])
def test_tier_from_text(text, tier):
    assert tier_from_text(text) == tier


def test_place_label():
    assert place_label("ExampleConfTallinn, Tallinn, Estonia") == "Tallinn, Estonia"
    assert place_label("Las Vegas Convention Center, 3150 Paradise Rd, Las Vegas, NV") == "3150 Paradise Rd, Las Vegas, NV"


def test_global_section_keeps_events_anywhere_with_tier(cfg, make_event):
    abroad = make_event("ExampleConfTallinn", group="talks", category="talk", address="ExampleConfTallinn, Tallinn, Estonia")
    us = make_event("ExampleConf Orlando", group="talks", category="talk", address="ExampleConfOrlando, Orlando, FL, USA")
    far_coords = make_event("ExampleConf Vegas", group="talks", category="talk", lat=36.1316, lon=-115.1511,
                            address="Las Vegas Convention Center, Las Vegas, NV, USA")
    local = make_event("Python meetup", group="talks", category="talk", lat=40.7359, lon=-73.9911)
    kept, rej = enrich([abroad, us, far_coords, local], cfg, None)
    assert rej == []
    assert [(e.title, e.extra["tier"]) for e in kept] == [
        ("ExampleConfTallinn", "international"), ("ExampleConf Orlando", "us"), ("ExampleConf Vegas", "us"),
        ("Python meetup", "metro")]
    assert kept[0].extra["place"] == "Tallinn, Estonia" and kept[3].borough == "Manhattan"


def test_local_groups_still_rejected_outside(cfg, make_event):
    e = make_event("Concert in Philly", group="shows", category="concert", lat=39.9526, lon=-75.1652)
    kept, rej = enrich([e], cfg, None)
    assert kept == [] and rej[0].reason == "out_of_region"


def test_route_tier_filter(cfg, make_event):
    from nymetro_eventlocator.config import Route
    r = Route("talks-near", {"group": "talks", "tiers": ["metro", "us"]}, "file:talks")
    assert matches(r, make_event("a", group="talks", extra={"tier": "us"}), cfg)
    assert not matches(r, make_event("b", group="talks", extra={"tier": "international"}), cfg)
