"""Install a daily `nymetro_eventlocator run` with the operating system's own scheduler.

Linux    systemd user timer   ~/.config/systemd/user/nymetro_eventlocator.{service,timer}
macOS    launchd agent        ~/Library/LaunchAgents/org.nymetro_eventlocator.daily.plist
Windows  Task Scheduler       task "nymetro_eventlocator" (runs as you, when you're logged on)

Each plan is data (files to write + commands to run), so `--print` can show it and tests can check it.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

LABEL = "org.nymetro_eventlocator.daily"


@dataclass
class Plan:
    system: str
    files: dict[Path, str] = field(default_factory=dict)
    commands: list[list[str]] = field(default_factory=list)
    remove_files: list[Path] = field(default_factory=list)
    status: list[str] = field(default_factory=list)

    def describe(self) -> str:
        out = [f"# scheduler: {self.system}"]
        for path, content in self.files.items():
            out += [f"# --- write {path}", content.rstrip()]
        for p in self.remove_files:
            out.append(f"# --- delete {p}")
        out += [" ".join(_quote(a) for a in cmd) for cmd in self.commands]
        return "\n".join(out)


def _quote(a: str) -> str:
    return f'"{a}"' if (" " in a or not a) else a


def _parse_time(hhmm: str) -> tuple[int, int]:
    h, m = (int(x) for x in hhmm.split(":"))
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"--time must be HH:MM, got {hhmm!r}")
    return h, m


def detect() -> str:
    if sys.platform.startswith("linux"):
        return "systemd"
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("win"):
        return "schtasks"
    raise RuntimeError(f"no scheduler support for {sys.platform}; run `nymetro_eventlocator run` from cron or similar")


def _command(python: str, config: Path) -> list[str]:
    return [python, "-m", "nymetro_eventlocator", "-c", str(config), "run"]


def plan_install(system: str, *, python: str, config: Path, time: str = "09:00", home: Path | None = None,
                 uid: int | None = None) -> Plan:
    h, m = _parse_time(time)
    home = home or Path.home()
    root = config.parent
    cmd = _command(python, config)
    if system == "systemd":
        unit_dir = home / ".config" / "systemd" / "user"
        service = (
            "[Unit]\nDescription=nymetro_eventlocator: fetch events, filter, update the page\n\n"
            "[Service]\nType=oneshot\n"
            f"WorkingDirectory={root}\n"
            f"ExecStart={' '.join(_quote(a) for a in cmd)}\n"
            "Nice=10\nIOSchedulingClass=idle\nTimeoutStartSec=45min\nUMask=0077\nNoNewPrivileges=yes\n"
        )
        timer = (
            "[Unit]\nDescription=Run nymetro_eventlocator every day\n\n"
            f"[Timer]\nOnCalendar=*-*-* {h:02d}:{m:02d}:00\nPersistent=true\nRandomizedDelaySec=10min\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
        return Plan(system, files={unit_dir / "nymetro_eventlocator.service": service, unit_dir / "nymetro_eventlocator.timer": timer},
                    commands=[["systemctl", "--user", "daemon-reload"],
                              ["systemctl", "--user", "enable", "--now", "nymetro_eventlocator.timer"]],
                    status=["systemctl", "--user", "list-timers", "nymetro_eventlocator.timer"])
    if system == "launchd":
        plist_path = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        log = root / "data" / "nymetro_eventlocator.log"
        plist = plistlib.dumps({
            "Label": LABEL, "ProgramArguments": cmd, "WorkingDirectory": str(root),
            "StartCalendarInterval": {"Hour": h, "Minute": m},
            "StandardOutPath": str(log), "StandardErrorPath": str(log), "ProcessType": "Background",
        }).decode()
        domain = f"gui/{uid if uid is not None else os.getuid()}"
        return Plan(system, files={plist_path: plist},
                    commands=[["launchctl", "bootout", domain, str(plist_path)],  # ok to fail if not loaded yet
                              ["launchctl", "bootstrap", domain, str(plist_path)]],
                    status=["launchctl", "print", f"{domain}/{LABEL}"])
    if system == "schtasks":
        return Plan(system, commands=[[
            "schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", f"{h:02d}:{m:02d}", "/TN", "nymetro_eventlocator",
            "/TR", " ".join(_quote(a) for a in cmd)]],
            status=["schtasks", "/Query", "/TN", "nymetro_eventlocator", "/V", "/FO", "LIST"])
    raise ValueError(f"unknown scheduler {system!r}")


def plan_remove(system: str, *, home: Path | None = None, uid: int | None = None) -> Plan:
    home = home or Path.home()
    if system == "systemd":
        unit_dir = home / ".config" / "systemd" / "user"
        return Plan(system, commands=[["systemctl", "--user", "disable", "--now", "nymetro_eventlocator.timer"],
                                      ["systemctl", "--user", "daemon-reload"]],
                    remove_files=[unit_dir / "nymetro_eventlocator.service", unit_dir / "nymetro_eventlocator.timer"])
    if system == "launchd":
        plist_path = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        return Plan(system, commands=[["launchctl", "bootout", f"gui/{uid if uid is not None else os.getuid()}",
                                       str(plist_path)]], remove_files=[plist_path])
    if system == "schtasks":
        return Plan(system, commands=[["schtasks", "/Delete", "/F", "/TN", "nymetro_eventlocator"]])
    raise ValueError(f"unknown scheduler {system!r}")


def execute(plan: Plan) -> int:
    """Write files, run commands. `launchctl bootout` failing (not loaded yet) is expected and ignored."""
    for path, content in plan.files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    rc = 0
    for cmd in plan.commands:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 and not (cmd[:2] == ["launchctl", "bootout"]):
            print(f"failed: {' '.join(cmd)}\n{proc.stderr.strip()}", file=sys.stderr)
            rc = 1
    for p in plan.remove_files:
        if p.exists():
            p.unlink()
    return rc
