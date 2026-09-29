"""`nymetro_eventlocator add <link>`: turn a Meetup group / Luma calendar / .ics link into a calendar feed,
preview what it would bring in, and add it to config.yaml (keeping the file's comments)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from nymetro_eventlocator.http.polite import FetchError, PoliteClient


class AddError(ValueError):
    pass


@dataclass
class Resolved:
    ical_url: str
    name: str      # suggested feed name
    kind: str      # "meetup" | "luma" | "ics"
    title: str = ""


_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "feed"


def resolve(link: str, client: PoliteClient | None) -> Resolved:
    link = link.strip()
    if link.startswith("webcal://"):
        link = "https://" + link[len("webcal://"):]
    if not re.match(r"^https?://", link):
        link = "https://" + link
    parts = urlsplit(link)
    host = parts.netloc.lower().removeprefix("www.")
    path = [p for p in parts.path.split("/") if p]

    if host == "meetup.com":
        if path and _LOCALE.match(path[0]):
            path = path[1:]  # e.g. /de-DE/<group>/
        if not path or path[0] in {"find", "topics", "lp", "cities", "apps", "home"}:
            raise AddError("that isn't a Meetup group link; open the group's page and copy its address")
        group = path[0]
        return Resolved(f"https://www.meetup.com/{group}/events/ical/", _slug(group), "meetup")

    if host in {"lu.ma", "luma.com", "api.lu.ma"}:
        cal = parse_qs(parts.query).get("id", [""])[0]
        if host == "api.lu.ma" and cal.startswith("cal-"):
            return Resolved(link, _slug(cal), "luma")
        if len(path) >= 2 and path[0] == "calendar" and path[1].startswith("cal-"):
            cal = path[1]
            return Resolved(f"https://api.lu.ma/ics/get?entity=calendar&id={cal}", _slug(cal), "luma")
        if not path:
            raise AddError("that's Luma's home page; open a calendar's page and copy its address")
        if client is None:
            raise AddError("need network access to look up the Luma calendar")
        cal, title = _luma_calendar_id(f"https://luma.com/{path[0]}", client)
        return Resolved(f"https://api.lu.ma/ics/get?entity=calendar&id={cal}", _slug(title or path[0]), "luma", title)

    if parts.path.lower().endswith(".ics") or "ical" in parts.path.lower() or "ics" in parse_qs(parts.query):
        return Resolved(link, _slug(host + "-" + (path[-1] if path else "calendar")), "ics")

    raise AddError("not a Meetup group, Luma calendar or .ics link. Look on the site for 'iCal', "
                   "'Subscribe' or 'Add to calendar' and use that link.")


def _luma_calendar_id(page_url: str, client: PoliteClient) -> tuple[str, str]:
    """The page's OWN calendar (not other calendars it mentions): initialData.data.calendar.api_id."""
    try:
        resp = client.get(page_url)
    except FetchError as e:
        raise AddError(f"couldn't open {page_url}: {e}") from e
    if resp.status != 200:
        raise AddError(f"{page_url} returned HTTP {resp.status}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
    try:
        data = json.loads(m.group(1))["props"]["pageProps"]["initialData"] if m else {}
    except (ValueError, KeyError, TypeError):
        data = {}
    if data.get("kind") == "event":
        raise AddError("that's a single Luma event; open the calendar it belongs to and use that link")
    cal = ((data.get("data") or {}).get("calendar") or {})
    if data.get("kind") != "calendar" or not str(cal.get("api_id", "")).startswith("cal-"):
        raise AddError("couldn't find this Luma page's calendar; on the page choose 'Add iCal Subscription' "
                       "and use that link instead")
    return cal["api_id"], cal.get("name", "")


# --- editing config.yaml without losing comments -----------------------------------------------

def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def feed_line(name: str, url: str, category: str | None) -> str:
    extra = f", category: {category}" if category else ""
    return f'{{ name: {name}, url: "{url}"{extra} }}'


def insert_feed(text: str, name: str, url: str, category: str | None) -> str:
    """Add a feed under sources.ical.feeds, keeping every other line (and comment) as it was."""
    lines = text.splitlines(keepends=True)
    item = feed_line(name, url, category)

    def find(pattern: str, start: int, stop: int, max_indent: int | None = None) -> int | None:
        for i in range(start, stop):
            if re.match(pattern, lines[i]) and (max_indent is None or _indent(lines[i]) <= max_indent):
                return i
        return None

    def block_end(start: int, parent_indent: int) -> int:
        """First line after `start` that belongs to a sibling/parent (indent <= parent_indent)."""
        for i in range(start + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped and not stripped.startswith("#") and _indent(lines[i]) <= parent_indent:
                return i
        return len(lines)

    src = find(r"^sources:\s*(#.*)?$", 0, len(lines))
    if src is None:
        raise AddError("config.yaml has no `sources:` section")
    src_end = block_end(src, 0)
    ical = find(r"^\s+ical:\s*(#.*)?$", src + 1, src_end)
    if ical is None:
        raise AddError("config.yaml has no `sources: ical:` section")
    ical_ind = _indent(lines[ical])
    ical_end = block_end(ical, ical_ind)
    feeds = find(r"^\s+feeds:\s*(\[\s*\])?\s*(#.*)?$", ical + 1, ical_end)
    if feeds is None:  # no feeds key yet: add one at the end of the ical block
        ind = " " * (ical_ind + 2)
        lines.insert(ical_end, f"{ind}feeds:\n{ind}  - {item}\n")
        return "".join(lines)
    f_ind = _indent(lines[feeds])
    if re.match(r"^\s+feeds:\s*\[\s*\]", lines[feeds]):  # `feeds: []`
        comment = re.search(r"#.*$", lines[feeds].rstrip("\n"))
        lines[feeds] = f"{' ' * f_ind}feeds:{('   ' + comment.group(0)) if comment else ''}\n{' ' * (f_ind + 2)}- {item}\n"
        return "".join(lines)
    # Existing list: items may be indented (`  - {..}`) or not (`- name: ..`); insert after the last item.
    item_ind, last = None, feeds
    for i in range(feeds + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        ind = _indent(lines[i])
        if stripped.startswith("- ") and ind >= f_ind and (item_ind is None or ind == item_ind):
            item_ind, last = ind, i
        elif item_ind is not None and ind > item_ind:
            last = i  # continuation of a multi-line item
        else:
            break
    ind = item_ind if item_ind is not None else f_ind + 2
    lines.insert(last + 1, f"{' ' * ind}- {item}\n")
    return "".join(lines)


def add_to_config(path: Path, name: str, url: str, category: str | None, load) -> None:
    """Insert, then verify with the real config loader; restore the original on any problem."""
    original = path.read_text(encoding="utf-8")
    try:
        cfg = load(path)
    except Exception as e:
        raise AddError(f"config.yaml doesn't load right now ({e}); fix that first") from e
    existing = cfg.sources.get("ical", {}).get("feeds") or []
    if any(f.get("url") == url for f in existing):
        raise AddError("that feed is already in config.yaml")
    if any(f.get("name") == name for f in existing):
        raise AddError(f"a feed named {name!r} already exists; choose another with --name")
    if category and cfg.category(category) is None:
        raise AddError(f"no interest called {category!r}; yours are: {', '.join(c.id for c in cfg.categories) or '(none)'}")
    path.write_text(insert_feed(original, name, url, category), encoding="utf-8")
    try:
        cfg = load(path)
        ok = any(f.get("url") == url for f in cfg.sources.get("ical", {}).get("feeds") or [])
    except Exception as e:
        ok, err = False, e
    else:
        err = None
    if not ok:
        path.write_text(original, encoding="utf-8")
        raise AddError(f"couldn't add the feed safely{f' ({err})' if err else ''}; config.yaml was left unchanged")
