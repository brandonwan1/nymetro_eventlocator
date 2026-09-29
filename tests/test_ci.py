"""Guards for the CI setup: third-party code that runs in CI is pinned, and versions agree across files."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def test_workflows_parse_and_have_read_only_default_permissions():
    assert WORKFLOWS
    for wf in WORKFLOWS:
        data = yaml.safe_load(wf.read_text(encoding="utf-8"))
        assert data["permissions"] == {"contents": "read"}, wf.name


def test_every_action_is_pinned_to_a_full_commit_sha():
    for wf in WORKFLOWS:
        for uses in re.findall(r"uses:\s*(\S+)", wf.read_text(encoding="utf-8")):
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", uses), f"{wf.name}: {uses} is not SHA-pinned"


def test_gitleaks_version_matches_pre_commit_hook():
    ci = re.search(r'GITLEAKS_VERSION:\s*"([\d.]+)"', (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")).group(1)
    hook = re.search(r"rev:\s*v([\d.]+)", (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")).group(1)
    assert ci == hook


def test_ruff_hook_matches_locked_version():
    hooks = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    hook = re.search(r"ruff-pre-commit\s+rev:\s*v([\d.]+)", hooks).group(1)
    locked = re.search(r"^ruff==([\d.]+)", (ROOT / "requirements-dev.lock").read_text(encoding="utf-8"), re.M).group(1)
    assert hook == locked
