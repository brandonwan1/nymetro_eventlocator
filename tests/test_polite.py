import ast
import itertools
from pathlib import Path

import pytest
from werkzeug import Response as WResponse

from nymetro_eventlocator.config import HttpSettings
from nymetro_eventlocator.http.polite import FetchError, HostBlocked, PoliteClient, RobotsDisallowed

PKG = Path(__file__).resolve().parent.parent / "nymetro_eventlocator"


class FakeClock:
    """Time only moves when the client sleeps, so delay tests are instant and exact."""

    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(store, clock):
    c = PoliteClient(store, HttpSettings(), clock=clock, sleep=clock.sleep)
    yield c
    c.close()


def robots(httpserver, body=None, status=200):
    httpserver.expect_request("/robots.txt").respond_with_data(body or "", status=status)


def recorder(httpserver, clock, path, body="ok", status=200, headers=None):
    times = []

    def handler(req):
        times.append(clock.t)
        return WResponse(body, status=status, headers=headers or {})

    httpserver.expect_request(path).respond_with_handler(handler)
    return times


def gaps(times):
    return [b - a for a, b in itertools.pairwise(times)]


def test_disallowed_path_is_never_requested(httpserver, client, clock):
    robots(httpserver, "User-agent: *\nDisallow: /private\n")
    hits = recorder(httpserver, clock, "/private/page")
    ok = recorder(httpserver, clock, "/public")
    with pytest.raises(RobotsDisallowed):
        client.get(httpserver.url_for("/private/page"))
    assert hits == []
    assert client.get(httpserver.url_for("/public")).text == "ok"
    assert len(ok) == 1
    assert client.stats.skipped_robots == 1


def test_rules_for_our_user_agent_apply(httpserver, client, clock):
    robots(httpserver, "User-agent: nymetro_eventlocator\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    hits = recorder(httpserver, clock, "/x")
    with pytest.raises(RobotsDisallowed):
        client.get(httpserver.url_for("/x"))
    assert hits == []


def test_crawl_delay_is_honored(httpserver, client, clock):
    robots(httpserver, "User-agent: *\nCrawl-delay: 10\n")
    times = recorder(httpserver, clock, "/p")
    for _ in range(3):
        client.get(httpserver.url_for("/p"), conditional=False)
    assert all(g >= 10 for g in gaps(times)), gaps(times)


def test_request_rate_is_honored(httpserver, client, clock):
    robots(httpserver, "User-agent: *\nRequest-rate: 1/20\n")
    times = recorder(httpserver, clock, "/p")
    for _ in range(3):
        client.get(httpserver.url_for("/p"), conditional=False)
    assert all(g >= 20 for g in gaps(times)), gaps(times)


def test_no_robots_txt_means_5s(httpserver, client, clock):
    robots(httpserver, "not found", status=404)
    times = recorder(httpserver, clock, "/p")
    for _ in range(3):
        client.get(httpserver.url_for("/p"), conditional=False)
    assert all(g >= 5 for g in gaps(times)), gaps(times)


def test_robots_without_delay_means_5s(httpserver, client, clock):
    robots(httpserver, "User-agent: *\nDisallow: /admin\n")
    times = recorder(httpserver, clock, "/p")
    for _ in range(3):
        client.get(httpserver.url_for("/p"), conditional=False)
    assert all(g >= 5 for g in gaps(times)), gaps(times)


def test_robots_403_means_no_rules_with_5s(httpserver, client, clock):
    robots(httpserver, "forbidden", status=403)
    times = recorder(httpserver, clock, "/p")
    client.get(httpserver.url_for("/p"), conditional=False)
    client.get(httpserver.url_for("/p"), conditional=False)
    assert gaps(times)[0] >= 5


@pytest.mark.parametrize("status", [500, 503, 429])
def test_robots_unavailable_blocks_host(httpserver, client, clock, store, status):
    robots(httpserver, "down", status=status)
    hits = recorder(httpserver, clock, "/p")
    with pytest.raises(HostBlocked):
        client.get(httpserver.url_for("/p"))
    assert hits == []
    assert store.robots_get(httpserver.url_for("/").rstrip("/")) is None  # a transient outage isn't cached


def test_retry_after_is_honored_then_succeeds(httpserver, client, clock):
    robots(httpserver, "User-agent: *\n")
    calls = []

    def handler(req):
        calls.append(clock.t)
        if len(calls) == 1:
            return WResponse("slow down", status=429, headers={"Retry-After": "42"})
        return WResponse("ok")

    httpserver.expect_request("/p").respond_with_handler(handler)
    assert client.get(httpserver.url_for("/p")).text == "ok"
    assert 42 in clock.sleeps
    assert calls[1] - calls[0] >= 42


def test_gives_up_after_max_retries(httpserver, client, clock):
    robots(httpserver, "User-agent: *\n")
    times = recorder(httpserver, clock, "/p", status=503)
    with pytest.raises(FetchError, match="after 2 retries"):
        client.get(httpserver.url_for("/p"))
    assert len(times) == 3
    assert all(g >= 5 for g in gaps(times))


def test_huge_retry_after_skips_instead_of_waiting(httpserver, client, clock):
    robots(httpserver, "User-agent: *\n")
    recorder(httpserver, clock, "/p", status=429, headers={"Retry-After": "86400"})
    with pytest.raises(FetchError, match="too long"):
        client.get(httpserver.url_for("/p"))
    assert 86400 not in clock.sleeps


def test_redirect_target_is_robots_checked(httpserver, client, clock):
    robots(httpserver, "User-agent: *\nDisallow: /secret\n")
    recorder(httpserver, clock, "/go", status=302, headers={"Location": "/secret"})
    hits = recorder(httpserver, clock, "/secret")
    with pytest.raises(RobotsDisallowed):
        client.get(httpserver.url_for("/go"))
    assert hits == []


def test_conditional_request_uses_cache(httpserver, client, clock):
    robots(httpserver, "User-agent: *\n")
    seen = []

    def handler(req):
        seen.append(req.headers.get("If-None-Match"))
        if req.headers.get("If-None-Match") == '"v1"':
            return WResponse(status=304)
        return WResponse("body-v1", headers={"ETag": '"v1"'})

    httpserver.expect_request("/feed").respond_with_handler(handler)
    first = client.get(httpserver.url_for("/feed"))
    second = client.get(httpserver.url_for("/feed"))
    assert first.text == second.text == "body-v1"
    assert second.from_cache and seen == [None, '"v1"']


def test_sends_identifying_user_agent(httpserver, client, clock):
    robots(httpserver, "User-agent: *\n")
    got = []
    httpserver.expect_request("/ua").respond_with_handler(lambda r: got.append(r.headers["User-Agent"]) or WResponse("ok"))
    client.get(httpserver.url_for("/ua"))
    from nymetro_eventlocator import __version__
    assert got == [f"nymetro_eventlocator/{__version__} (personal, non-commercial)"]


def test_robots_is_cached_across_clients(httpserver, store, clock):
    robots(httpserver, "User-agent: *\n")
    recorder(httpserver, clock, "/p")
    for _ in range(2):
        c = PoliteClient(store, HttpSettings(), clock=clock, sleep=clock.sleep)
        c.get(httpserver.url_for("/p"), conditional=False)
        c.close()
    robots_hits = [r for r, _ in httpserver.log if r.path == "/robots.txt"]
    assert len(robots_hits) == 1


def test_secrets_are_masked_and_not_cached(httpserver, client, clock, store, caplog):
    robots(httpserver, "User-agent: *\n")
    recorder(httpserver, clock, "/api", status=503, headers={"ETag": '"x"'})
    with pytest.raises(FetchError) as err:
        client.get(httpserver.url_for("/api"), params={"apikey": "SEKRET123"}, secrets=("SEKRET123",))
    assert "SEKRET123" not in str(err.value) and "***" in str(err.value)
    ours = [r.getMessage() for r in caplog.records if not r.name.startswith("werkzeug")]  # werkzeug = the test server
    assert not any("SEKRET123" in m for m in ours)
    assert store.conn.execute("SELECT COUNT(*) FROM http_cache").fetchone()[0] == 0


def test_non_http_scheme_refused(client):
    with pytest.raises(RobotsDisallowed):
        client.get("file:///etc/passwd")


FORBIDDEN_MODULES = {"httpx", "requests", "urllib.request", "urllib3", "aiohttp", "playwright", "http.client", "socket"}


def test_no_module_bypasses_the_gateway():
    """Only nymetro_eventlocator/http/ may talk to the network directly."""
    offenders = []
    for py in PKG.rglob("*.py"):
        if py.parent.name == "http" and py.parent.parent == PKG:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if any(n == m or n.startswith(m + ".") for m in FORBIDDEN_MODULES):
                    offenders.append(f"{py.relative_to(PKG)}: {n}")
    assert offenders == []
