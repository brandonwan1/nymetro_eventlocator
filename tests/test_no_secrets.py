"""Fails if anything that looks like a real secret is in a file git would commit."""

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "data", "output"}

PATTERNS = {
    "Discord webhook": re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d{17,20}/[\w-]{40,}"),
    "Discord bot token": re.compile(r"\b[MNO][\w-]{23,27}\.[\w-]{6}\.[\w-]{27,40}\b"),
    "API key in a URL/assignment": re.compile(r"(?i)\b(?:apikey|api_key|token|secret)\s*[=:]\s*['\"]?([A-Za-z0-9]{24,})"),
    "Bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{30,}"),
    "Private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def tracked_files() -> list[Path]:
    """What git would commit: `git ls-files` (incl. untracked-but-not-ignored); falls back to a directory walk."""
    if (ROOT / ".git").exists() and shutil.which("git"):
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             cwd=ROOT, capture_output=True, check=True).stdout.decode()
        return [ROOT / p for p in out.split("\0") if p]
    files = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if p.is_file() and not (set(rel.parts) & SKIP_DIRS) and p.name not in {"config.yaml", "overrides.yaml", ".env"}:
            files.append(p)
    return files


def test_no_secret_shaped_strings_would_be_committed():
    hits = []
    for f in tracked_files():
        if f.suffix in {".cred"}:
            hits.append(f"{f.relative_to(ROOT)}: credential file")
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except (IsADirectoryError, PermissionError):
            continue
        for label, rx in PATTERNS.items():
            for m in rx.finditer(text):
                hits.append(f"{f.relative_to(ROOT)}: {label}: {m.group(0)[:12]}…")
    assert hits == []


def test_private_files_are_ignored():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    for must in ("config.yaml", "overrides.yaml", ".env", "*.cred", "/data/", "/output/", "*.db"):
        assert must in gitignore, must
    assert "!.env.example" in gitignore


def test_scanner_catches_real_shapes(tmp_path):
    fake_hook = "https://discord.com/api/webhooks/" + "1" * 18 + "/" + "a" * 68
    fake_key = "apikey=" + "Ab1" * 11
    for label, sample in (("Discord webhook", fake_hook), ("API key in a URL/assignment", fake_key)):
        assert PATTERNS[label].search(sample), label


def test_packaged_data_is_not_ignored():
    """Real bug 2026-09-26: a bare `data/` rule also ignored nymetro_eventlocator/geo/data/ (the boundary files)."""
    if not ((ROOT / ".git").exists() and shutil.which("git")):
        return
    tracked = {str(p.relative_to(ROOT)).replace("\\", "/") for p in tracked_files()}
    for needed in ("nymetro_eventlocator/geo/data/nyc_boroughs.geojson", "nymetro_eventlocator/geo/data/nyc_metro_counties.geojson", "nymetro_eventlocator/geo/data/nyc_metro_places.geojson",
                   "nymetro_eventlocator/render/templates/index.html.j2", "config.example.yaml", "overrides.example.yaml"):
        assert needed in tracked, needed
