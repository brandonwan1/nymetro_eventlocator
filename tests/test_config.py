from pathlib import Path

import pytest
import yaml

from nymetro_eventlocator.__main__ import main
from nymetro_eventlocator.config import ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tests" / "fixtures" / "test_config.yaml"


def base_raw() -> dict:
    """The test suite's config: made-up interests, so validation of categories can be exercised."""
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_example_config_is_a_neutral_catch_all():
    cfg = load_config(ROOT / "config.example.yaml")
    assert cfg.region.name == "nyc" and len(cfg.region.areas) == 2
    assert cfg.catch_all and list(cfg.groups) == ["all"]      # ships with nobody's interests
    assert cfg.sources["ical"]["feeds"] == [] and cfg.exclude_keywords == []
    assert not {"confstech", "manual"} & set(cfg.sources)
    assert cfg.http.default_delay >= 5


def test_test_config_loads():
    cfg = load_config(CONFIG)
    assert [c.id for c in cfg.categories][0] == "yoga" and cfg.categories[-1].fallback
    assert not cfg.catch_all


def test_env_vars_expand(tmp_path, monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "example-key")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    cfg = load_config(cfg_path)
    assert cfg.sources["ticketmaster"]["api_key"] == "example-key"


def test_example_config_has_no_notifiers_or_routes():
    raw = base_raw()
    assert "notifiers" not in raw and "routes" not in raw
    cfg = load_config(ROOT / "config.example.yaml")
    assert cfg.routes == []


def with_route(r: dict) -> dict:
    """Routes are optional and unused by default; older configs may still have them."""
    r["notifiers"] = {"file": {}}
    r["routes"] = [{"id": "shows", "match": {"group": "shows"}, "to": "file:shows"}]
    return r


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda r: r.pop("region"), "missing required key 'region'"),
        (lambda r: r["categories"][0].update(group="nope"), "group 'nope' is not defined"),
        (lambda r: r["categories"].append(dict(r["categories"][0])), "duplicate category id"),
        (lambda r: r["categories"][0].update(keywords=[], patterns=[]), "needs keywords, patterns or sources"),
        (lambda r: r["categories"][0].update(patterns=["("]), "bad pattern"),
        (lambda r: with_route(r)["routes"][0].update(to="shows"), "must look like 'output:name'"),
        (lambda r: with_route(r)["routes"][0].update(to="email:shows"), "notifier 'email' is not configured"),
        (lambda r: with_route(r)["routes"][0]["match"].update(group="nope"), "unknown group"),
        (lambda r: with_route(r)["routes"][0]["match"].update(near="gym"), "unknown place"),
        (lambda r: r.update(http={"default_delay": 1}), "at least 5 seconds"),
        (lambda r: r["region"].update(center=[1]), "expected [lat, lon]"),
        (lambda r: r["region"]["areas"].append({"file": "nope.geojson", "name_key": "x"}), "boundary file not found"),
    ],
)
def test_invalid_config_is_rejected_clearly(mutate, message):
    raw = base_raw()
    mutate(raw)
    with pytest.raises(ConfigError, match=message.replace("[", r"\[").replace("(", r"\(")):
        parse_config(raw, root=ROOT)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_cli_check_config(capsys):
    assert main(["-c", str(ROOT / "config.example.yaml"), "check-config"]) == 0
    assert "config ok" in capsys.readouterr().out


def test_cli_bad_config_exit_code(tmp_path, capsys):
    bad = tmp_path / "config.yaml"
    bad.write_text("region: {}\n", encoding="utf-8")
    assert main(["-c", str(bad), "check-config"]) == 2
    assert "config error" in capsys.readouterr().err


def test_removed_keys_are_ignored():
    raw = base_raw()
    raw["notify_window_days"] = 30   # removed 2026-09-26; older configs still have it
    assert parse_config(raw, root=ROOT).region.name == "nyc"


def test_old_config_with_routes_still_loads():
    cfg = parse_config(with_route(base_raw()), root=ROOT)
    assert [r.to for r in cfg.routes] == ["file:shows"]


BLANK = ("region: {name: nyc, timezone: America/New_York, center: [40.71, -74.0], bbox: [39.47, -75.20, 41.53, -71.77],\n"
         "         areas: [{file: nyc_boroughs.geojson, name_key: boroname}, {file: nyc_metro_counties.geojson, name_key: name}]}\n")


def test_blank_slate_is_catch_all(tmp_path, capsys):
    """No sections, categories or routes: valid, and every event goes to a built-in 'All events' section."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(BLANK + "groups: {}\ncategories: []\nroutes: []\nsources: {ical: {enabled: true, feeds: []}}\n", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.catch_all
    assert cfg.groups == {"all": {"label": "All events"}}
    assert [(c.id, c.group, c.fallback) for c in cfg.categories] == [("all", "all", True)]
    assert main(["-c", str(cfg_path), "check-config"]) == 0
    out = capsys.readouterr().out
    assert "keeping every event" in out and "All events" in out and "runs will keep nothing" not in out


def test_check_config_warns_when_no_sources(tmp_path, capsys):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(BLANK, encoding="utf-8")
    assert main(["-c", str(cfg_path), "check-config"]) == 0
    assert "no sources are enabled" in capsys.readouterr().out


def test_blank_slate_run_keeps_everything(tmp_path, store, make_event):
    from nymetro_eventlocator.pipeline import process
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(BLANK + "exclude_keywords: [toddler]\n", encoding="utf-8")
    cfg = load_config(cfg_path)
    brooklyn = dict(lat=40.6782, lon=-73.9442)
    events = [
        make_event("Jazz night", **brooklyn),
        make_event("Book swap", **brooklyn),
        make_event("Toddler music time", **brooklyn),
        make_event("Board games", description="for toddler parents", **brooklyn),
        make_event("Philly street fair", lat=39.9526, lon=-75.1652),
        make_event("Zoom book chat", is_online=True),
        make_event("Rooftop party POSTPONED", **brooklyn),
    ]
    rep = process(events, cfg, store, None)
    assert sorted(e.title for e in rep.kept) == ["Book swap", "Jazz night"]
    assert all((e.group, e.category) == ("all", "all") for e in rep.kept)
    assert all(e.tags == [] for e in rep.kept)
    reasons = {r.event.title: r.reason for r in rep.rejected}
    assert reasons == {"Toddler music time": "excluded_keyword", "Board games": "excluded_keyword",
                       "Philly street fair": "out_of_region", "Zoom book chat": "online_only",
                       "Rooftop party POSTPONED": "cancelled"}
    assert "route_filter" not in reasons.values()


def test_first_category_turns_filtering_on(tmp_path, store, make_event):
    from nymetro_eventlocator.pipeline import process
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(BLANK + "groups: {outdoors: {label: Outdoors}}\n"
                        "categories: [{id: hiking, group: outdoors, keywords: [hike]}]\n", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert not cfg.catch_all and "all" not in cfg.groups
    brooklyn = dict(lat=40.6782, lon=-73.9442)
    rep = process([make_event("Group hike", **brooklyn), make_event("Jazz night", **brooklyn)], cfg, store, None)
    assert [(e.title, e.category) for e in rep.kept] == [("Group hike", "hiking")]
    assert [(r.event.title, r.reason) for r in rep.rejected] == [("Jazz night", "no_category")]
