from pathlib import Path

import pytest

from nymetro_eventlocator.classify import Classifier, Overrides, phrase_regex
from nymetro_eventlocator.config import load_config

CONFIG = Path(__file__).resolve().parent / "fixtures" / "test_config.yaml"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG)


@pytest.fixture
def clf(cfg):
    return Classifier(cfg)


def one(clf, e):
    kept, rejected = clf.classify([e])
    return (kept[0] if kept else None), (rejected[0] if rejected else None)


@pytest.mark.parametrize(
    "title, category, group",
    [
        ("Sunrise yoga in the park", "yoga", "outdoors"),
        ("Vinyasa flow", "yoga", "outdoors"),
        ("Yoga then drinks and live music at the bar", "yoga", "outdoors"),  # the earlier category wins
        ("Hiking the Palisades", "hiking", "outdoors"),
        ("Trail walk + coffee", "hiking", "outdoors"),
        ("Lightning talks: data pipelines", "talk", "talks"),
        ("Board game night in Gowanus", "board-games", "games"),
        ("Catan league", "board-games", "games"),
        ("Pub trivia", "trivia", "games"),
        ("Indie band in concert", "concert", "shows"),
        ("Stand-up comedy night", "comedy", "shows"),
        ("A mixer for new neighbors", "community", "community"),
        ("Trivia mixer", "trivia", "games"),                                 # specific beats the fallback
    ],
)
def test_categories_and_groups(clf, make_event, title, category, group):
    kept, rej = one(clf, make_event(title))
    assert rej is None, rej
    assert (kept.category, kept.group) == (category, group)


@pytest.mark.parametrize("title, term", [
    ("Casino night", "casino"),
    ("Toddler story time", "toddler"),
    ("Magic show for kids", "for kids"),
    ("CASINO + trivia", "casino"),  # exclusion wins even if a category matches
])
def test_excluded_words(clf, make_event, title, term):
    kept, rej = one(clf, make_event(title))
    assert kept is None
    assert (rej.reason, rej.detail, rej.stage) == ("excluded_keyword", term, "exclude")


def test_exclude_word_only_in_description_keeps_title_match(clf, make_event):
    e = make_event("Saturday hike", description="5 mile loop, then some of us visit the casino")
    kept, rej = one(clf, e)
    assert rej is None and kept.category == "hiking"


def test_exclude_word_in_description_rejects_when_title_is_generic(clf, make_event):
    e = make_event("Sunday Signup", description="Casino bus trip, all welcome")
    kept, rej = one(clf, e)
    assert kept is None and rej.reason == "excluded_keyword"


def test_unmatched_is_no_category(clf, make_event):
    kept, rej = one(clf, make_event("Tax preparation seminar"))
    assert kept is None and rej.reason == "no_category" and rej.stage == "classify"


def test_title_beats_description(clf, make_event):
    e = make_event("Morning yoga", description="Afterwards we go to a concert")
    kept, _ = one(clf, e)
    assert kept.category == "yoga"


def test_description_used_when_title_is_vague(clf, make_event):
    e = make_event("Saturday session", description="Weekly hiking group")
    kept, _ = one(clf, e)
    assert kept.category == "hiking" and kept.extra["matched"]["where"] == "description"


def test_whole_words_only(clf, make_event):
    # "tour" must not match "tournament", "band" not "bandana", "hike" not "hiker"
    for title in ("Chess tournament", "Bandana tie-dye workshop", "Hiker gear swap"):
        kept, rej = one(clf, make_event(title))
        assert kept is None and rej.reason == "no_category", title


def test_hyphen_and_space_interchangeable():
    rx = phrase_regex("stand-up")
    assert rx.search("Stand Up comedy") and rx.search("STAND-UP") and rx.search("standup")
    assert rx.search("standuppaddle") is None
    assert phrase_regex("board game").search("Board-Game")


def test_matched_keyword_becomes_a_tag(clf, make_event):
    kept, _ = one(clf, make_event("Pub trivia"))
    assert kept.tags == ["trivia"]


def test_overrides(cfg, make_event):
    a = make_event("Tax preparation seminar", url="https://x.test/a")
    b = make_event("Trivia night", url="https://x.test/b")
    clf = Classifier(cfg, Overrides(force_include={"https://x.test/a": "concert"}, force_exclude={"https://x.test/b"}))
    kept, rej = clf.classify([a, b])
    assert [(k.url, k.category, k.group) for k in kept] == [("https://x.test/a", "concert", "shows")]
    assert [(r.event.url, r.reason) for r in rej] == [("https://x.test/b", "manual_exclude")]


def test_override_with_unknown_category_fails(cfg):
    with pytest.raises(ValueError, match="unknown category"):
        Classifier(cfg, Overrides(force_include={"u": "nope"}))


def test_overrides_file_loads(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_text("force_include:\n  https://a: hiking\nforce_exclude:\n  - https://b\n", encoding="utf-8")
    o = Overrides.load(p)
    assert o.force_include == {"https://a": "hiking"} and o.force_exclude == {"https://b"}
    assert Overrides.load(tmp_path / "missing.yaml").force_include == {}


def test_rejections_are_recorded(store, clf, make_event):
    _, rejected = clf.classify([make_event("Casino"), make_event("Tax seminar")])
    for r in rejected:
        store.record_rejection(r)
    assert {r["reason"] for r in store.rejections()} == {"excluded_keyword", "no_category"}


def test_title_only_ignores_boilerplate_description(clf, make_event):
    # Ticketmaster descriptions are venue policies, not about the event
    e = make_event("An Evening with Example Singer", description="Board games and trivia in the lobby before the show.",
                   extra={"title_only": True, "category_hint": "concert"})
    kept, _ = one(clf, e)
    assert kept.category == "concert" and kept.extra["matched"]["where"] == "hint"


def test_kids_events_excluded(clf, make_event):
    kept, rej = one(clf, make_event("Toddler Dance World Tour"))
    assert kept is None and rej.detail == "toddler"


def test_hint_used_when_title_has_no_keyword(clf, make_event):
    kept, _ = one(clf, make_event("The Midnight Giants", extra={"title_only": True, "category_hint": "concert"}))
    assert kept.category == "concert"


def test_fallback_loses_to_hint_and_specific_description(clf, make_event):
    hiking_group = make_event("Tuesday meetup", extra={"category_hint": "hiking"})
    described = make_event("Saturday meetup", description="Weekly hiking group")
    plain = make_event("Saturday meetup")
    kept, _ = clf.classify([hiking_group, described, plain])
    assert [(e.title, e.category) for e in kept] == [
        ("Tuesday meetup", "hiking"), ("Saturday meetup", "hiking"), ("Saturday meetup", "community")]


def test_keyword_in_venue_name_ignored_when_match_venue_false(clf, make_event):
    # "python" (a talk keyword) must not match the venue "Python Point Park"
    e = make_event("Saturday hike", venue_name="Python Point Park")
    kept, rej = one(clf, e)
    assert rej is None and (kept.category, kept.group) == ("hiking", "outdoors")


def test_match_venue_false_ignores_venue_but_others_still_use_it(clf, make_event):
    kept, rej = one(clf, make_event("Monthly meetup", venue_name="Python Cafe"))
    assert kept.group != "talks"
    kept, _ = one(clf, make_event("Saturday lineup", venue_name="Somewhere Comedy Club"))
    assert kept.category == "comedy"


@pytest.mark.parametrize("title, word", [
    ("ExampleConf Lisbon POSTPONED", "POSTPONED"),     # some organizers mark it only in the title
    ("ExampleConf Manama POSTPONED TO 2027", "POSTPONED"),
    ("CANCELLED: Tempo Thursdays", "CANCELLED"),
    ("Yoga canceled this week", "canceled"),
])
def test_called_off_titles_rejected(clf, make_event, title, word):
    kept, rej = one(clf, make_event(title))
    assert kept is None and rej.reason == "cancelled" and word in rej.detail


def test_normal_title_not_called_off(clf, make_event):
    kept, rej = one(clf, make_event("ExampleConNYC"))
    assert rej is None and kept.category == "talk"
