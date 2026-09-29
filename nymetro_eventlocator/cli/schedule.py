from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NAME = "schedule"
NEEDS_CONFIG = True


def add_parser(sub) -> None:
    sch = sub.add_parser(NAME, help="run nymetro_eventlocator daily via systemd (Linux), launchd (macOS) or Task Scheduler (Windows)")
    schs = sch.add_subparsers(dest="schedule_cmd", required=True)
    s_inst = schs.add_parser("install", help="install / update the daily run")
    s_inst.add_argument("--time", default="09:00", help="HH:MM local time (default 09:00)")
    s_inst.add_argument("--print", dest="print_only", action="store_true", help="show what would be installed; change nothing")
    s_rm = schs.add_parser("remove", help="remove the daily run")
    s_rm.add_argument("--print", dest="print_only", action="store_true")
    schs.add_parser("status", help="show the scheduler's view of the daily run")


def run(args, cfg) -> int:
    from nymetro_eventlocator import schedule as SCH

    system = SCH.detect()
    config = Path(args.config).resolve()
    if args.schedule_cmd == "status":
        return subprocess.run(SCH.plan_install(system, python=sys.executable, config=config).status).returncode
    plan = (SCH.plan_install(system, python=sys.executable, config=config, time=args.time)
            if args.schedule_cmd == "install" else SCH.plan_remove(system))
    if args.print_only:
        print(plan.describe())
        return 0
    rc = SCH.execute(plan)
    print(("installed" if args.schedule_cmd == "install" else "removed") + f" ({system})" if rc == 0 else "see errors above")
    return rc
