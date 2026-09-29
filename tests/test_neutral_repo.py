"""The repo ships no one's personal interests: no specific groups, calendars, venues or hobbies the maintainer
follows. Users add their own with `init`, `add <link>` and their private config.yaml. This list names what was
removed on 2026-09-26 so it can't creep back in; extend it if you remove more."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BANNED = [
    # interests
    r"language exchange", r"intercambio", r"japanese", r"korean", r"pickleball", r"jiu.?jitsu", r"\bbjj\b",
    r"open mat", r"bouldering", r"techno", r"warehouse party", r"cybersecurity", r"infosec", r"hackathon",
    r"figure drawing", r"pottery", r"cooking class", r"volleyball", r"soccer", r"run club",
    # specific groups, calendars, events and venues
    r"lexgo", r"langroops", r"owasp", r"nylug", r"bsides", r"def ?con\b", r"hackers on planet", r"tmirce",
    r"founders running", r"zogsports", r"mundo lingo", r"the skint", r"monitorss", r"550 madison",
    r"nycgatherings", r"bkrun", r"nycrunclubs", r"brightnights", r"brooklyn mirage", r"movement gowanus",
    r"brooklyn boulders", r"bluestone", r"pitch ?and ?run", r"elsewhere\.club", r"599 johnson",
    r"cal-2r1wr2q80eww9am", r"cal-1mzzrlu2ixzrs5v", r"cal-cnuvqbpxdjdzgcf", r"cal-aipap0crgks7tcw",
]
PATTERN = re.compile("|".join(BANNED), re.IGNORECASE)


def shipped_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [f for f in out.splitlines() if f and f != "tests/test_neutral_repo.py"]


def test_no_personal_interests_in_shipped_files():
    hits = []
    for rel in shipped_files():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        hits += [f"{rel}:{i}: {m.group(0)}" for i, line in enumerate(text.splitlines(), 1)
                 for m in [PATTERN.search(line)] if m]
    assert hits == []
