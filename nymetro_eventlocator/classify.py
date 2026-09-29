"""Exclude + categorize stage. Rule-based only.

Matching:
- Keywords match whole words/phrases, case-insensitively; spaces and hyphens are interchangeable ("stand-up" == "stand up").
- Categories are tried in config order against the title (+ venue) first, then against the description,
  so a keyword in the title beats a stray mention in a long description.
- An exclude keyword in the title always rejects. One that only appears in the description rejects
  only if the title doesn't match a category (e.g. "Group hike ... casino trip after" is kept).
- A source may set extra["title_only"] when its descriptions are boilerplate (Ticketmaster).
- A source/feed may give a category_hint, used only when no specific keyword matches.
- Categories marked `fallback: true` (general social) are tried last, after hints.
- A title saying POSTPONED / CANCELLED is rejected as `cancelled`.
- Categories with `match_venue: false` ignore the venue name (career topics).
- overrides.yaml can force an event (by url) into a category or out entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from nymetro_eventlocator.config import Category, Config
from nymetro_eventlocator.models import Event, Rejection

CALLED_OFF = re.compile(r"\b(postponed|cancell?ed)\b", re.IGNORECASE)


def phrase_regex(phrase: str) -> re.Pattern:
    words = [re.escape(w) for w in re.split(r"[\s\-]+", phrase.strip()) if w]
    return re.compile(r"(?<!\w)" + r"[\s\-]*".join(words) + r"(?!\w)", re.IGNORECASE)


@dataclass
class Overrides:
    force_include: dict[str, str] = field(default_factory=dict)
    force_exclude: set[str] = field(default_factory=set)
    venues: dict[str, tuple[float, float]] = field(default_factory=dict)  # venue name -> (lat, lon), case-insensitive

    @classmethod
    def load(cls, path: str | Path) -> Overrides:
        p = Path(path)
        if not p.exists():
            return cls()
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        venues = {str(k).strip().lower(): (float(v[0]), float(v[1])) for k, v in (raw.get("venues") or {}).items()}
        return cls(dict(raw.get("force_include") or {}), set(raw.get("force_exclude") or []), venues)


@dataclass
class Match:
    category: Category
    keyword: str
    where: str  # "title" | "description" | "source" | "hint" | "override" | "catch_all"


class Classifier:
    def __init__(self, cfg: Config, overrides: Overrides | None = None):
        self.cfg = cfg
        self.overrides = overrides or Overrides()
        self._cats = [
            (c, [(k, phrase_regex(k)) for k in c.keywords] + [(p, re.compile(p, re.IGNORECASE)) for p in c.patterns])
            for c in cfg.categories
        ]
        self._exclude = [(k, phrase_regex(k)) for k in cfg.exclude_keywords]
        for url, cat in self.overrides.force_include.items():
            if cfg.category(cat) is None:
                raise ValueError(f"overrides.yaml: force_include {url!r} names unknown category {cat!r}")

    def _keyword_match(self, e: Event, fallback: bool) -> Match | None:
        with_venue = f"{e.title} {e.venue_name}"
        passes = ["title"] + ([] if e.extra.get("title_only") else ["description"])  # some descriptions are boilerplate
        for where in passes:
            for cat, rules in self._cats:
                if cat.fallback != fallback:
                    continue
                if where == "description":
                    text = e.description
                else:  # match_venue: false never matches a venue name ("Python Cafe" isn't a Python talk)
                    text = with_venue if cat.match_venue else e.title
                for kw, rx in rules:
                    if rx.search(text):
                        return Match(cat, kw, where)
        return None

    def match(self, e: Event) -> Match | None:
        """Specific keywords (title, then description) -> source rule -> feed hint -> fallback categories."""
        if self.cfg.catch_all:  # no interests configured: everything lands in the built-in "all" category
            return Match(self.cfg.categories[0], "all", "catch_all")
        m = self._keyword_match(e, fallback=False)
        if m:
            return m
        for cat, _ in self._cats:
            if e.source in cat.sources:
                return Match(cat, e.source, "source")
        hint = e.extra.get("category_hint")
        if hint and self.cfg.category(hint):
            return Match(self.cfg.category(hint), hint, "hint")
        return self._keyword_match(e, fallback=True)

    def excluded_by(self, e: Event, m: Match | None) -> str | None:
        for kw, rx in self._exclude:
            if rx.search(e.title):
                return kw
            if not e.extra.get("title_only") and rx.search(e.description) and (m is None or m.where != "title"):
                return kw
        return None

    def classify(self, events: list[Event]) -> tuple[list[Event], list[Rejection]]:
        kept: list[Event] = []
        rejected: list[Rejection] = []
        for e in events:
            if e.url in self.overrides.force_exclude:
                rejected.append(Rejection(e, "classify", "manual_exclude", "overrides.yaml"))
                continue
            off = CALLED_OFF.search(e.title)
            if off:  # some organizers mark "Summit POSTPONED" only in the title
                rejected.append(Rejection(e, "classify", "cancelled", f"'{off.group(0)}' in title"))
                continue
            forced = self.overrides.force_include.get(e.url)
            if forced and self.cfg.category(forced) is None:
                forced = None  # e.g. an override naming an interest that was since removed
            if forced:
                m = Match(self.cfg.category(forced), forced, "override")
            else:
                m = self.match(e)
                bad = self.excluded_by(e, m)
                if bad:
                    rejected.append(Rejection(e, "exclude", "excluded_keyword", bad))
                    continue
            if m is None:
                rejected.append(Rejection(e, "classify", "no_category"))
                continue
            e.category, e.group = m.category.id, m.category.group
            e.extra["matched"] = {"keyword": m.keyword, "where": m.where}
            tags = [m.keyword] if m.where in ("title", "description") else []
            e.tags = list(dict.fromkeys(e.tags + tags))
            kept.append(e)
        return kept, rejected
