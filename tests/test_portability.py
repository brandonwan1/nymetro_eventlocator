import ast
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nymetro_eventlocator.timefmt import fmt_day, fmt_day_time, fmt_time

PKG = Path(__file__).resolve().parent.parent / "nymetro_eventlocator"
NY = ZoneInfo("America/New_York")


def test_time_labels():
    assert fmt_day(datetime(2026, 9, 6, 21, 5, tzinfo=NY)) == "Sun Sep 6"
    assert fmt_time(datetime(2026, 9, 6, 21, 5, tzinfo=NY)) == "9:05 PM"
    assert fmt_time(datetime(2026, 9, 6, 0, 30, tzinfo=NY)) == "12:30 AM"
    assert fmt_time(datetime(2026, 9, 6, 12, 0, tzinfo=NY)) == "12:00 PM"
    assert fmt_day_time(datetime(2026, 10, 17, 9, 0, tzinfo=NY)) == "Sat Oct 17, 9:00 AM"


def test_no_unix_only_code():
    """Keep the package runnable on Windows: no fcntl/pwd/grp imports, no %-d style strftime codes."""
    offenders = []
    for py in PKG.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            offenders += [f"{py.name}: import {n}" for n in names if n.split(".")[0] in {"fcntl", "pwd", "grp", "termios"}]
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "strftime":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "%-" in arg.value:
                        offenders.append(f"{py.name}: strftime({arg.value!r})")
    assert offenders == []


def test_run_lock_blocks_second_run(tmp_path):
    from filelock import FileLock, Timeout
    import pytest
    a, b = FileLock(str(tmp_path / "run.lock")), FileLock(str(tmp_path / "run.lock"))
    a.acquire(timeout=0)
    with pytest.raises(Timeout):
        b.acquire(timeout=0)
    a.release()
    b.acquire(timeout=0)
    b.release()


def test_text_files_always_use_utf8():
    """Windows defaults to a legacy encoding (cp1252): without encoding="utf-8", configs with non-English
    names or emoji would be misread, or crash, on Windows only. Caught by CI on 2026-09-27."""
    import ast
    pkg = Path(__file__).resolve().parent.parent / "nymetro_eventlocator"
    missing = []
    for py in pkg.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            is_text_io = name in ("read_text", "write_text") or (isinstance(node.func, ast.Name) and name == "open")
            if is_text_io and not any(k.arg == "encoding" for k in node.keywords):
                missing.append(f"{py.relative_to(pkg.parent)}:{node.lineno} {name}()")
    assert missing == []
