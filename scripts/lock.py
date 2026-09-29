"""Regenerate requirements.lock and requirements-dev.lock from pyproject.toml.

The lock files pin every dependency (and its dependencies) to exact versions with hashes, for every OS:
`--universal` keeps platform markers, so e.g. keyring's Windows-only packages are included for Windows users
even when the lock is generated on Linux.

    pip install uv            # once, into your dev venv; users never need it
    python scripts/lock.py            # re-pin, keeping current versions where possible
    python scripts/lock.py --upgrade  # move everything to the newest allowed versions

Then run the tests (`pip install -r requirements-dev.lock && pytest`) and commit both files; CI tests the
pinned versions on Linux, macOS and Windows.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCKS = {"requirements.lock": [], "requirements-dev.lock": ["--extra", "dev"]}


def main(argv: list[str]) -> int:
    uv = shutil.which("uv") or shutil.which("uv", path=str(Path(sys.executable).parent))
    if not uv:
        print("uv not found: pip install uv", file=sys.stderr)
        return 2
    upgrade = ["--upgrade"] if "--upgrade" in argv else []
    for out, extra in LOCKS.items():
        cmd = [uv, "pip", "compile", "pyproject.toml", *extra, *upgrade,
               "--universal", "--python-version", "3.12", "--generate-hashes", "--quiet",
               "--custom-compile-command", "python scripts/lock.py", "-o", out]
        if subprocess.run(cmd, cwd=ROOT).returncode:
            return 1
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
