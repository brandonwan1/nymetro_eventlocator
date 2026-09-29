"""The project is a local aggregator: nothing is posted anywhere, so Discord isn't mentioned in shipped files.
(tests/test_no_secrets.py keeps its Discord token patterns on purpose: it's a generic secret scanner.)"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"tests/test_no_secrets.py", "tests/test_no_discord.py"}


def shipped_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [f for f in out.splitlines() if f]


def test_no_discord_mentions():
    hits = []
    for rel in shipped_files():
        if rel in ALLOWED:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        hits += [f"{rel}:{i}" for i, line in enumerate(text.splitlines(), 1) if "discord" in line.lower()]
    assert hits == []
