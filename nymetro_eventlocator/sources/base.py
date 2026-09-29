"""Source plugins. Each source declares how it accesses the site (its rung on the access ladder) and a ToS note.

Sources must fetch only through the PoliteClient they're given (enforced by tests/test_polite.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from nymetro_eventlocator.config import Config
from nymetro_eventlocator.http.polite import PoliteClient
from nymetro_eventlocator.models import Event, Rejection

ACCESS_RUNGS = ("manual", "feed", "official_api", "html", "playwright")  # manual = typed into config, never fetched

REGISTRY: dict[str, type[Source]] = {}


def register_source(name: str):
    def deco(cls: type[Source]) -> type[Source]:
        if cls.access not in ACCESS_RUNGS:
            raise ValueError(f"source {name}: access must be one of {ACCESS_RUNGS}")
        if not cls.tos_note:
            raise ValueError(f"source {name}: tos_note is required (see docs/sources-compliance.md)")
        if cls.access == "playwright" and not cls.playwright_justification:
            raise ValueError(f"source {name}: playwright sources need playwright_justification")
        cls.name = name
        REGISTRY[name] = cls
        return cls
    return deco


@dataclass
class SourceResult:
    events: list[Event] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Scopes (feed/page/organizer names, or "*" for the whole source) that were NOT fully fetched this run.
    # Stored events from incomplete scopes are never treated as gone.
    incomplete: set[str] = field(default_factory=set)


def scope_of(e: Event) -> str:
    """Which part of its source an event came from (a feed, page or followed organizer)."""
    return e.extra.get("feed") or e.extra.get("page") or e.extra.get("follow") or "*"


class Source(ABC):
    name: ClassVar[str] = ""
    access: ClassVar[str] = ""
    tos_note: ClassVar[str] = ""
    playwright_justification: ClassVar[str] = ""

    def __init__(self, settings: dict, cfg: Config):
        self.settings = settings
        self.cfg = cfg

    @abstractmethod
    def fetch(self, client: PoliteClient) -> SourceResult: ...


def load_all() -> dict[str, type[Source]]:
    """Import every module in this package so their @register_source decorators run."""
    import importlib
    import pkgutil

    import nymetro_eventlocator.sources as pkg

    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name != "base":
            importlib.import_module(f"nymetro_eventlocator.sources.{m.name}")
    return REGISTRY
