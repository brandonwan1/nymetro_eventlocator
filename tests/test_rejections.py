from datetime import datetime, timedelta, UTC
from pathlib import Path

import pytest

from nymetro_eventlocator.__main__ import main
from nymetro_eventlocator.models import Rejection
from nymetro_eventlocator.rejections import format_summary, parse_since, summarize

ROOT = Path(__file__).resolve().parent.parent


def seed(store, make_event):
    for i in range(3):
        store.record_rejection(Rejection(make_event(f"Casino {i}", source="ical"), "exclude", "excluded_keyword", "casino"))
    store.record_rejection(Rejection(make_event("Toddler time", source="ical"), "exclude", "excluded_keyword", "toddler"))
    store.record_rejection(Rejection(make_event("Tax seminar", source="ical"), "classify", "no_category"))
    store.record_rejection(Rejection(make_event("MetLife show", source="ticketmaster"), "geo", "out_of_region", "40.81,-74.07"))


def test_summary_counts_and_breakdown(store, make_event):
    seed(store, make_event)
    total, by_reason, details = summarize(store)
    assert total == 6 and by_reason["excluded_keyword"] == 4
    assert details["excluded_keyword"] == {"casino": 3, "toddler": 1}
    text = format_summary(store)
    assert text.startswith("Rejected 6: excluded_keyword 4 (casino 3, toddler 1)")
    assert "no_category 1 (ical 1)" in text and "out_of_region 1 (ticketmaster 1)" in text


def test_empty_summary(store):
    assert format_summary(store) == "Rejected 0"


def test_parse_since():
    now = datetime.now(UTC)
    assert abs((now - parse_since("7d")) - timedelta(days=7)) < timedelta(seconds=5)
    assert abs((now - parse_since("12h")) - timedelta(hours=12)) < timedelta(seconds=5)
    assert parse_since(None) is None
    with pytest.raises(ValueError):
        parse_since("last week")


def test_cli_rejected(tmp_path, capsys, make_event):
    from nymetro_eventlocator.db.store import Store
    cfg_text = (ROOT / "config.example.yaml").read_text(encoding="utf-8").replace("\nregion:", "\ndb_path: test.db\nregion:", 1)
    (tmp_path / "config.yaml").write_text(cfg_text, encoding="utf-8")
    store = Store(tmp_path / "test.db")
    seed(store, make_event)
    store.close()
    assert main(["-c", str(tmp_path / "config.yaml"), "rejected", "--reason", "excluded_keyword", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Rejected 6:") and out.count("excluded_keyword (") == 2 and "... 2 more" in out
    assert main(["-c", str(tmp_path / "config.yaml"), "rejected", "--summary"]) == 0
    assert capsys.readouterr().out.count("\n") == 1
    assert main(["-c", str(tmp_path / "config.yaml"), "rejected", "--since", "yesterday"]) == 2
