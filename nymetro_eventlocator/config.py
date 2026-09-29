"""Load and validate config.yaml. Everything that might change later (categories, routes, region) lives there."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from nymetro_eventlocator import __version__


class ConfigError(ValueError):
    pass


@dataclass
class Region:
    name: str
    timezone: str
    center: tuple[float, float]
    bbox: tuple[float, float, float, float]  # south, west, north, east
    areas: list[dict] = field(default_factory=list)          # [{file, name_key}] boundary files; empty = bbox only
    neighborhoods: list[dict] = field(default_factory=list)


@dataclass
class Category:
    id: str
    group: str
    keywords: list[str]
    patterns: list[str] = field(default_factory=list)  # raw regexes
    sources: list[str] = field(default_factory=list)  # any event from these sources also matches
    fallback: bool = False  # only considered after specific categories and source/feed hints
    match_venue: bool = True  # False: keywords are matched against the title only, never the venue name


@dataclass
class Route:
    id: str
    match: dict[str, Any]
    to: str  # "<output>:<name>"; routes are optional and unused by default


@dataclass
class HttpSettings:
    user_agent: str = f"nymetro_eventlocator/{__version__} (personal, non-commercial)"
    default_delay: float = 5.0
    timeout: float = 30.0
    max_retries: int = 2
    robots_cache_hours: float = 24.0


@dataclass
class Config:
    region: Region
    places: dict[str, tuple[float, float] | None]
    groups: dict[str, dict]
    categories: list[Category]
    exclude_keywords: list[str]
    sources: dict[str, dict]
    notifiers: dict[str, dict]
    routes: list[Route]
    exclude_online: bool
    db_path: Path
    output_dir: Path
    http: HttpSettings
    root: Path
    web: dict = field(default_factory=dict)  # web page options, e.g. {"map": {"style_light": url, ...}}
    catch_all: bool = False  # no interests configured: keep every event in one built-in "All events" section

    def category(self, cat_id: str) -> Category | None:
        return next((c for c in self.categories if c.id == cat_id), None)


GEO_DATA = Path(__file__).parent / "geo" / "data"
CATCH_ALL = "all"  # id of the built-in section/category used when no interests are configured

_ENV = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value: Any) -> Any:
    """${VAR} -> the secret's value: systemd credential first, then environment/.env (see nymetro_eventlocator/secrets.py)."""
    from nymetro_eventlocator.secrets import resolve

    if isinstance(value, str):
        return _ENV.sub(lambda m: resolve(m.group(1)), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _require(d: dict, key: str, where: str) -> Any:
    if key not in d or d[key] is None:
        raise ConfigError(f"{where}: missing required key '{key}'")
    return d[key]


def _pair(v: Any, where: str) -> tuple[float, float]:
    if not (isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v)):
        raise ConfigError(f"{where}: expected [lat, lon], got {v!r}")
    return float(v[0]), float(v[1])


def _boundary_list(v: Any, where: str) -> list[dict]:
    out = []
    for i, b in enumerate(v or []):
        f = _require(b, "file", f"{where}[{i}]")
        _require(b, "name_key", f"{where}[{i}]")
        if not (GEO_DATA / f).exists():
            raise ConfigError(f"{where}[{i}]: boundary file not found: {f}")
        out.append({"file": f, "name_key": b["name_key"]})
    return out


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path).resolve()
    if not path.exists():
        example = path.with_name("config.example.yaml")
        hint = f" (copy {example.name} to {path.name} and edit it)" if example.exists() else ""
        raise ConfigError(f"config file not found: {path}{hint}")
    load_dotenv(path.parent / ".env")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name}: invalid YAML: {e}") from e
    from nymetro_eventlocator.secrets import SecretError, configure

    try:
        configure(raw.get("secrets_backend"))  # before ${VAR}s are resolved
    except SecretError as e:
        raise ConfigError(str(e)) from e
    raw = _expand_env(raw)
    return parse_config(raw, root=path.parent)


def parse_config(raw: dict, root: Path) -> Config:
    r = _require(raw, "region", "config")
    bbox = _require(r, "bbox", "region")
    if not (isinstance(bbox, list) and len(bbox) == 4):
        raise ConfigError("region.bbox: expected [south, west, north, east]")
    region = Region(
        name=_require(r, "name", "region"),
        timezone=_require(r, "timezone", "region"),
        center=_pair(_require(r, "center", "region"), "region.center"),
        bbox=tuple(float(x) for x in bbox),
        areas=_boundary_list(r.get("areas"), "region.areas"),
        neighborhoods=_boundary_list(r.get("neighborhoods"), "region.neighborhoods"),
    )

    places = {k: (_pair(v, f"places.{k}") if v is not None else None) for k, v in (raw.get("places") or {}).items()}

    groups = raw.get("groups") or {}  # empty = no interests configured yet (a blank slate is valid)
    if not isinstance(groups, dict):
        raise ConfigError("groups: must be a mapping of section id -> settings")

    categories: list[Category] = []
    seen: set[str] = set()
    for i, c in enumerate(raw.get("categories") or []):
        where = f"categories[{i}]"
        cid = _require(c, "id", where)
        if cid in seen:
            raise ConfigError(f"{where}: duplicate category id '{cid}'")
        seen.add(cid)
        group = _require(c, "group", f"category '{cid}'")
        if group not in groups:
            raise ConfigError(f"category '{cid}': group '{group}' is not defined under groups")
        kws = c.get("keywords") or []
        pats = c.get("patterns") or []
        if not kws and not pats and not c.get("sources"):
            raise ConfigError(f"category '{cid}': needs keywords, patterns or sources")
        for p in pats:
            try:
                re.compile(p)
            except re.error as e:
                raise ConfigError(f"category '{cid}': bad pattern {p!r}: {e}") from e
        categories.append(Category(cid, group, [k.lower() for k in kws], pats, c.get("sources") or [],
                                   fallback=bool(c.get("fallback", False)),
                                   match_venue=bool(c.get("match_venue", True))))

    catch_all = not categories
    if catch_all:  # no interests = keep everything (safety filters still apply); reuses the fallback mechanism
        groups = {**groups, CATCH_ALL: {"label": "All events"}}
        categories.append(Category(CATCH_ALL, CATCH_ALL, [], [r"."], fallback=True))

    notifiers = raw.get("notifiers") or {}  # optional, unused: kept so older configs still load
    routes: list[Route] = []
    for i, rt in enumerate(raw.get("routes") or []):
        where = f"routes[{i}]"
        match = _require(rt, "match", where)
        to = _require(rt, "to", where)
        if ":" not in to:
            raise ConfigError(f"{where}: 'to' must look like 'output:name', got {to!r}")
        kind, channel = to.split(":", 1)
        if kind not in notifiers:
            raise ConfigError(f"{where}: notifier '{kind}' is not configured under notifiers")
        for key, known in (("group", groups), ("category", seen)):
            values = match.get(key, [])
            for v in values if isinstance(values, list) else [values]:
                if v not in known:
                    raise ConfigError(f"{where}: unknown {key} '{v}'")
        if "near" in match and match["near"] not in places:
            raise ConfigError(f"{where}: unknown place '{match['near']}'")
        routes.append(Route(id=rt.get("id") or f"{to}#{i}", match=match, to=to))

    http = HttpSettings(**(raw.get("http") or {}))
    if http.default_delay < 5:
        raise ConfigError("http.default_delay: must be at least 5 seconds")

    return Config(
        region=region,
        places=places,
        groups=groups,
        categories=categories,
        exclude_keywords=[k.lower() for k in raw.get("exclude_keywords") or []],
        sources=raw.get("sources") or {},
        notifiers=notifiers,
        routes=routes,
        exclude_online=bool(raw.get("exclude_online", True)),
        db_path=root / raw.get("db_path", "data/events.db"),
        output_dir=root / raw.get("output_dir", "output"),
        http=http,
        root=root,
        web=raw.get("web") or {},
        catch_all=catch_all,
    )
