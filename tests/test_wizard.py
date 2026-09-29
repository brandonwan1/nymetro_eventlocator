import yaml

from nymetro_eventlocator import wizard as W
from nymetro_eventlocator.__main__ import main
from nymetro_eventlocator.config import load_config


def test_starter_config_is_valid_catch_all(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(W.render(W.build_config()), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.catch_all and list(cfg.groups) == ["all"]
    assert cfg.region.name == "nyc" and len(cfg.region.areas) == 2 and len(cfg.region.neighborhoods) == 2
    assert cfg.sources["ical"]["enabled"] and cfg.sources["ical"]["feeds"] == []
    assert cfg.sources["ticketmaster"]["enabled"] and not cfg.sources["eventbrite"]["enabled"]
    assert "No interests yet" in path.read_text(encoding="utf-8")


def test_starter_config_names_no_interests_and_no_secret_values():
    text = W.render(W.build_config())
    raw = yaml.safe_load(text)
    assert raw["groups"] == {} and raw["categories"] == [] and raw["exclude_keywords"] == []
    assert "${TICKETMASTER_API_KEY}" in text and "${EVENTBRITE_TOKEN}" in text   # references, never values
    assert "notifiers" not in raw and "routes" not in raw and "webhook" not in text.lower()


def test_build_config_returns_independent_copies():
    a, b = W.build_config(), W.build_config()
    a["sources"]["ticketmaster"]["enabled"] = False
    assert b["sources"]["ticketmaster"]["enabled"] is True


def test_cli_init_writes_and_protects_existing(tmp_path, capsys):
    cfg = tmp_path / "config.yaml"
    assert main(["-c", str(cfg), "init"]) == 0
    out = capsys.readouterr().out
    assert "no interest filtering" in out and "add <meetup, luma or .ics link>" in out
    assert load_config(cfg).catch_all
    first = cfg.read_text(encoding="utf-8")
    cfg.write_text(first + "# my edits\n", encoding="utf-8")
    assert main(["-c", str(cfg), "init"]) == 2                           # refuses to overwrite
    assert cfg.read_text(encoding="utf-8").endswith("# my edits\n")
    assert main(["-c", str(cfg), "init", "--force"]) == 0
    backups = list(tmp_path.glob("config.backup-*.yaml"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8").endswith("# my edits\n")   # old config kept


def test_cli_init_copies_overrides_example_once(tmp_path):
    (tmp_path / "overrides.example.yaml").write_text("venues: {}\n", encoding="utf-8")
    assert main(["-c", str(tmp_path / "config.yaml"), "init"]) == 0
    assert (tmp_path / "overrides.yaml").read_text(encoding="utf-8") == "venues: {}\n"


def test_build_config_does_not_share_nested_settings():
    a, b = W.build_config(), W.build_config()
    a["region"]["areas"].clear()
    a["sources"]["ticketmaster"]["searches"].clear()
    assert b["region"]["areas"] and b["sources"]["ticketmaster"]["searches"] and W.BASE["region"]["areas"]
