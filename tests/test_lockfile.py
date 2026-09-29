"""The lock files must cover pyproject.toml's dependencies (CI also checks they're fully up to date)."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _names(reqs: list[str]) -> set[str]:
    return {re.split(r"[<>=!~;\[ ]", r, maxsplit=1)[0].lower().replace("_", "-") for r in reqs}


def _pinned(lock: str) -> dict[str, str]:
    text = (ROOT / lock).read_text(encoding="utf-8")
    return {m[0].lower(): m[1] for m in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", text, re.M)}


def test_locks_pin_every_declared_dependency_with_hashes():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    runtime = _names(project["dependencies"])
    dev = _names(project["optional-dependencies"]["dev"])
    assert runtime <= set(_pinned("requirements.lock"))
    assert runtime | dev <= set(_pinned("requirements-dev.lock"))
    for lock in ("requirements.lock", "requirements-dev.lock"):
        text = (ROOT / lock).read_text(encoding="utf-8")
        reqs = [line for line in text.splitlines() if line and not line[0].isspace() and not line.startswith("#")]
        assert reqs and all(re.match(r"^[A-Za-z0-9_.-]+==\S+", r) for r in reqs)  # nothing unpinned
        assert "--hash=sha256:" in text


def test_dev_lock_agrees_with_runtime_lock():
    runtime, dev = _pinned("requirements.lock"), _pinned("requirements-dev.lock")
    assert {k: dev[k] for k in runtime} == runtime


def test_lock_keeps_other_platforms_dependencies():
    """Generated on Linux, but Windows users need keyring's Windows backend too (uv --universal)."""
    text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert re.search(r"^pywin32-ctypes==.*sys_platform == 'win32'", text, re.M)
