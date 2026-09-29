"""The only way nymetro_eventlocator fetches anything from the network.

Rules (see docs/sources-compliance.md):
- robots.txt is checked for every URL, including every redirect hop. Disallowed URLs are never requested.
- Delay between requests to the same host: the site's Crawl-delay/Request-rate if given, otherwise 5 seconds.
- robots.txt answering 5xx, 429 or failing on the network blocks the whole host for this run (RFC 9309).
  Any other 4xx (incl. 401/403) means "no rules", so the default delay applies.
- 429/503 honor Retry-After, at most `max_retries` retries, then the fetch fails.
- Conditional requests (ETag / Last-Modified) avoid re-downloading unchanged pages.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from email.utils import parsedate_to_datetime
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import httpx
from protego import Protego

from nymetro_eventlocator.config import HttpSettings
from nymetro_eventlocator.db.store import Store

log = logging.getLogger(__name__)

MAX_REDIRECTS = 5
MAX_RETRY_AFTER = 300.0  # longer than this and we give up for today rather than wait


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


class HostBlocked(FetchError):
    pass


@dataclass
class Response:
    url: str
    status: int
    content: bytes
    headers: dict[str, str]
    from_cache: bool = False

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        import json
        return json.loads(self.content)


@dataclass
class RobotsPolicy:
    status: int
    parser: Protego | None  # None = no rules
    blocked: bool = False

    def allows(self, url: str, ua: str) -> bool:
        if self.blocked:
            return False
        return True if self.parser is None else self.parser.can_fetch(url, ua)

    def delay(self, ua: str, default: float) -> float:
        if self.parser is None:
            return default
        d = self.parser.crawl_delay(ua)
        if d is None:
            rate = self.parser.request_rate(ua)
            if rate and rate.requests:
                d = rate.seconds / rate.requests
        return float(d) if d is not None else default


@dataclass
class Decision:
    allowed: bool
    delay: float
    reason: str


@dataclass
class Stats:
    requests: int = 0
    skipped_robots: int = 0
    per_host: dict[str, int] = field(default_factory=dict)


class PoliteClient:
    def __init__(
        self,
        store: Store,
        settings: HttpSettings | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ):
        self.store = store
        self.s = settings or HttpSettings()
        self.clock = clock
        self.sleep = sleep
        self.stats = Stats()
        self._policies: dict[str, RobotsPolicy] = {}
        self._last_request: dict[str, float] = {}
        self._http = httpx.Client(
            headers={"User-Agent": self.s.user_agent},
            timeout=self.s.timeout,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # --- robots -------------------------------------------------------------------------

    def policy(self, origin: str) -> RobotsPolicy:
        if origin in self._policies:
            return self._policies[origin]
        cached = self.store.robots_get(origin)
        fresh = cached is not None and (
            datetime.now(UTC) - datetime.fromisoformat(cached["fetched_at"])
            < timedelta(hours=self.s.robots_cache_hours)
        )
        if fresh:
            status, body = cached["status"], cached["body"]
        else:
            status, body = self._fetch_robots(origin)
        pol = _policy_from(status, body)
        if not fresh and not pol.blocked:  # don't let a transient outage block the host for a whole day
            self.store.robots_put(origin, status, body)
        if pol.blocked:
            log.warning("robots.txt for %s unavailable (status %s): host blocked for this run", origin, status)
        self._policies[origin] = pol
        return pol

    def _fetch_robots(self, origin: str) -> tuple[int, str]:
        url = origin + "/robots.txt"
        try:
            self._wait_turn(origin, self.s.default_delay)
            r = self._http.get(url, follow_redirects=True)
            self._mark(origin)
        except httpx.HTTPError as e:
            log.warning("robots.txt fetch failed for %s: %s", origin, e)
            return 0, ""
        return r.status_code, r.text if 200 <= r.status_code < 300 else ""

    def check(self, url: str) -> Decision:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return Decision(False, 0, f"unsupported scheme {parts.scheme!r}")
        origin = f"{parts.scheme}://{parts.netloc}"
        pol = self.policy(origin)
        delay = pol.delay(self.s.user_agent, self.s.default_delay)
        if pol.blocked:
            return Decision(False, delay, f"robots.txt unavailable (status {pol.status}); host blocked")
        if not pol.allows(url, self.s.user_agent):
            return Decision(False, delay, "disallowed by robots.txt")
        return Decision(True, delay, "allowed" if pol.parser else "no robots.txt rules")

    # --- fetching -----------------------------------------------------------------------

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None, conditional: bool = True,
            secrets: tuple[str, ...] = ()) -> Response:
        """`secrets` (API keys in the URL) are masked in logs and errors, and such URLs are never cached to disk."""
        secrets = tuple(x for x in secrets if x)
        if not secrets:
            return self._get(url, params=params, headers=headers, conditional=conditional)
        self._secrets = secrets
        try:
            return self._get(url, params=params, headers=headers, conditional=False)
        except FetchError as e:
            raise type(e)(self._mask(str(e))) from None
        finally:
            self._secrets = ()

    def _mask(self, text: str) -> str:
        for sec in getattr(self, "_secrets", ()):
            text = text.replace(sec, "***")
        return text

    def _get(self, url: str, *, params: dict | None, headers: dict | None, conditional: bool) -> Response:
        if params:
            url = str(httpx.URL(url, params=params))
        cached = self.store.http_cache_get(url) if conditional else None
        for _ in range(MAX_REDIRECTS + 1):
            resp = self._get_one(url, headers or {}, cached)
            if resp.status in (301, 302, 303, 307, 308) and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                cached = self.store.http_cache_get(url) if conditional else None
                continue
            if resp.status == 304 and cached is not None:
                return Response(url, 200, cached["body"], resp.headers, from_cache=True)
            if conditional and resp.status == 200 and (resp.headers.get("etag") or resp.headers.get("last-modified")):
                self.store.http_cache_put(url, resp.headers.get("etag"), resp.headers.get("last-modified"), resp.content)
            return resp
        raise FetchError(f"too many redirects: {url}")

    def _get_one(self, url: str, headers: dict, cached) -> Response:
        decision = self.check(url)
        if not decision.allowed:
            self.stats.skipped_robots += 1
            log.info("SKIPPED (robots) %s: %s", self._mask(url), decision.reason)
            if "blocked" in decision.reason:
                raise HostBlocked(f"{url}: {decision.reason}")
            raise RobotsDisallowed(f"{url}: {decision.reason}")
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        h = dict(headers)
        if cached is not None:
            if cached["etag"]:
                h["If-None-Match"] = cached["etag"]
            if cached["last_modified"]:
                h["If-Modified-Since"] = cached["last_modified"]

        attempt = 0
        while True:
            self._wait_turn(origin, decision.delay)
            try:
                r = self._http.get(url, headers=h)
            except httpx.HTTPError as e:
                self._mark(origin)
                raise FetchError(f"{url}: {e}") from e
            self._mark(origin)
            self.stats.requests += 1
            self.stats.per_host[origin] = self.stats.per_host.get(origin, 0) + 1
            if r.status_code in (429, 503):
                if attempt >= self.s.max_retries:
                    raise FetchError(f"{url}: HTTP {r.status_code} after {attempt} retries")
                wait = _retry_after(r.headers.get("retry-after"))
                if wait is None:
                    wait = max(decision.delay, self.s.default_delay) * (2 ** (attempt + 1))
                if wait > MAX_RETRY_AFTER:
                    raise FetchError(f"{url}: HTTP {r.status_code}, Retry-After {wait:.0f}s is too long; skipping today")
                log.info("HTTP %s from %s, waiting %.0fs", r.status_code, self._mask(url), wait)
                self.sleep(wait)
                attempt += 1
                continue
            return Response(url, r.status_code, r.content, {k.lower(): v for k, v in r.headers.items()})

    def _wait_turn(self, origin: str, delay: float) -> None:
        last = self._last_request.get(origin)
        if last is not None:
            remaining = last + delay - self.clock()
            if remaining > 0:
                self.sleep(remaining)

    def _mark(self, origin: str) -> None:
        self._last_request[origin] = self.clock()


def _policy_from(status: int, body: str) -> RobotsPolicy:
    if status == 0 or status == 429 or status >= 500:
        return RobotsPolicy(status, None, blocked=True)
    if 200 <= status < 300:
        return RobotsPolicy(status, Protego.parse(body))
    return RobotsPolicy(status, None)  # other 4xx: no rules


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None
