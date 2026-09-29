import plistlib
from pathlib import Path, PurePosixPath

import pytest

from nymetro_eventlocator import schedule as S

# Linux/macOS plans are only ever built on Linux/macOS; PurePosixPath keeps "/" when the suite runs on Windows.
CFG = PurePosixPath("/opt/nymetro_eventlocator/config.yaml")
HOME = PurePosixPath("/home/tester")


def test_systemd_plan():
    p = S.plan_install("systemd", python="/opt/nymetro_eventlocator/.venv/bin/python", config=CFG, time="07:30", home=HOME)
    service = p.files[HOME / ".config/systemd/user/nymetro_eventlocator.service"]
    timer = p.files[HOME / ".config/systemd/user/nymetro_eventlocator.timer"]
    assert "ExecStart=/opt/nymetro_eventlocator/.venv/bin/python -m nymetro_eventlocator -c /opt/nymetro_eventlocator/config.yaml run" in service
    assert "WorkingDirectory=/opt/nymetro_eventlocator" in service and "NoNewPrivileges=yes" in service
    assert "OnCalendar=*-*-* 07:30:00" in timer and "Persistent=true" in timer
    assert p.commands[-1] == ["systemctl", "--user", "enable", "--now", "nymetro_eventlocator.timer"]


def test_launchd_plan():
    p = S.plan_install("launchd", python="/Users/t/.venv/bin/python", config=PurePosixPath("/Users/t/nymetro_eventlocator/config.yaml"),
                       time="09:00", home=PurePosixPath("/Users/t"), uid=501)
    path = PurePosixPath("/Users/t/Library/LaunchAgents/org.nymetro_eventlocator.daily.plist")
    d = plistlib.loads(p.files[path].encode())
    assert d["ProgramArguments"] == ["/Users/t/.venv/bin/python", "-m", "nymetro_eventlocator", "-c", "/Users/t/nymetro_eventlocator/config.yaml", "run"]
    assert d["StartCalendarInterval"] == {"Hour": 9, "Minute": 0} and d["WorkingDirectory"] == "/Users/t/nymetro_eventlocator"
    assert p.commands[-1] == ["launchctl", "bootstrap", "gui/501", str(path)]


def test_windows_plan_quotes_paths_with_spaces():
    p = S.plan_install("schtasks", python=r"C:\Program Files\Python312\python.exe",
                       config=Path(r"C:\Users\t\nymetro_eventlocator\config.yaml"), time="21:05")
    cmd = p.commands[0]
    assert cmd[:8] == ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", "21:05", "/TN"]
    tr = cmd[cmd.index("/TR") + 1]
    assert tr.startswith('"C:\\Program Files\\Python312\\python.exe" -m nymetro_eventlocator -c') and tr.endswith(" run")


def test_remove_plans_and_bad_time():
    assert S.plan_remove("schtasks").commands == [["schtasks", "/Delete", "/F", "/TN", "nymetro_eventlocator"]]
    assert S.plan_remove("systemd", home=HOME).remove_files[0].name == "nymetro_eventlocator.service"
    with pytest.raises(ValueError):
        S.plan_install("systemd", python="p", config=CFG, time="25:00")


def test_print_changes_nothing(tmp_path, capsys, monkeypatch):
    from nymetro_eventlocator.__main__ import main
    monkeypatch.setattr(S, "detect", lambda: "systemd")
    monkeypatch.setattr(S.Path, "home", staticmethod(lambda: tmp_path))
    root = Path(__file__).resolve().parent.parent
    assert main(["-c", str(root / "config.example.yaml"), "schedule", "install", "--print"]) == 0
    out = capsys.readouterr().out
    assert "OnCalendar=*-*-* 09:00:00" in out and not (tmp_path / ".config").exists()
