from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Event:
    """One event as seen by one source, normalized. Later stages fill in category, group and location."""

    source: str
    source_id: str  # stable id within the source (native id, or the url)
    title: str
    start: datetime  # timezone-aware
    url: str
    end: datetime | None = None
    description: str = ""
    venue_name: str = ""
    address: str = ""
    lat: float | None = None
    lon: float | None = None
    borough: str = ""
    neighborhood: str = ""
    is_online: bool = False
    price: str = ""
    image: str = ""
    category: str = ""
    group: str = ""
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.source}|{self.source_id}".encode()).hexdigest()[:16]

    def text(self) -> str:
        """Everything keyword rules look at."""
        return " ".join([self.title, self.description, self.venue_name])


@dataclass
class Rejection:
    event: Event
    stage: str
    reason: str
    detail: str = ""
