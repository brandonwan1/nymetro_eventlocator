import json
import re
from pathlib import Path

import yaml

from nymetro_eventlocator import demo
from nymetro_eventlocator.cli import main
from nymetro_eventlocator.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def page_data(path: Path) -> dict:
    html = path.read_text(encoding="utf-8")
    return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1))


def test_demo_page_shows_every_feature_without_network(tmp_path, monkeypatch):
    import httpx

    def no_network(*a, **kw):
        raise AssertionError("the demo must not use the network")
    monkeypatch.setattr(httpx.Client, "send", no_network)
    path, shown, rejected = demo.build(tmp_path / "demo.html")
    data = page_data(path)
    assert shown == len(data["events"]) == 15 and rejected == len(data["rejections"]) == 6
    assert set(data["groups"]) == {"outdoors", "games", "talks", "community"}
    assert {e["group"] for e in data["events"]} == set(data["groups"])            # every section has events
    assert sorted(r["reason"] for r in data["rejections"]) == sorted(
        ["cancelled", "duplicate", "excluded_keyword", "no_category", "online_only", "out_of_region"])
    assert all(e["lat"] is not None for e in data["events"])                       # all pins on the map
    assert all("example" in e["url"] for e in data["events"])                      # made-up links only


def test_demo_command_needs_no_config(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)                                                    # no config.yaml here
    assert main(["demo", "--out", "page/demo.html"]) == 0
    assert "15 sample events" in capsys.readouterr().out
    assert (tmp_path / "page" / "demo.html").exists() and not (tmp_path / "data").exists()   # no database left behind


def test_example_config_matches_the_demo():
    raw = yaml.safe_load((ROOT / "examples" / "config.filtered.yaml").read_text(encoding="utf-8"))
    for key in ("groups", "categories", "exclude_keywords"):
        assert raw[key] == demo.DEMO_INTERESTS[key], key
    cfg = load_config(ROOT / "examples" / "config.filtered.yaml")
    assert not cfg.catch_all and cfg.categories[-1].fallback
