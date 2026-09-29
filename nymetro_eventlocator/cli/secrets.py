from __future__ import annotations

import os
import sys
from pathlib import Path

NAME = "secrets"
NEEDS_CONFIG = False  # must work before config.yaml fully loads (its ${VAR}s may not resolve yet)


def add_parser(sub) -> None:
    sc = sub.add_parser(NAME, help="store API keys in the OS keyring or systemd-creds, never plaintext")
    scs = sc.add_subparsers(dest="secrets_cmd", required=True)
    scs.add_parser("list", help="show where each secret referenced in config.yaml comes from (never the values)")
    s_set = scs.add_parser("set", help="encrypt and store a secret; the value is read hidden from the terminal or stdin")
    s_set.add_argument("name", help="e.g. TICKETMASTER_API_KEY")
    s_imp = scs.add_parser("import-env", help="move secrets from .env into encrypted credentials and delete them from .env")
    s_imp.add_argument("names", nargs="*", help="default: every ${VAR} in config.yaml that is set in .env")
    s_del = scs.add_parser("delete", help="delete a stored credential")
    s_del.add_argument("name")


def run(args, cfg) -> int:
    import getpass

    import yaml
    from dotenv import load_dotenv

    from nymetro_eventlocator import secrets as S

    cfg_path = Path(args.config).resolve()  # read directly: works even when config.yaml can't fully load yet
    env_path = cfg_path.parent / ".env"
    load_dotenv(env_path)
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    names = S.referenced_vars(text) if text else []
    try:
        S.configure((yaml.safe_load(text) or {}).get("secrets_backend") if text else None)
        if args.secrets_cmd == "list":
            for st in S.status(names):
                print(f"  {st.var:28} {st.where}")
            print(f"backend: {S.backend().label}")
            if isinstance(S.backend(), S.SystemdCreds):
                print(f"credentials folder: {S.cred_dir()}")
            return 0
        if args.secrets_cmd == "set":
            value = getpass.getpass(f"{args.name} (input hidden): ") if sys.stdin.isatty() else sys.stdin.read()
            where = S.set_secret(args.name, value)
            print(f"stored in {where}")
            if S.remove_from_env_file(env_path, args.name):
                print(f"removed plaintext {args.name} from {env_path}")
            return 0
        if args.secrets_cmd == "import-env":
            todo = args.names or [n for n in names if os.environ.get(n)]
            if not todo:
                print("nothing to import: no referenced secrets are set in .env")
            for n in todo:
                value = os.environ.get(n, "")
                if not value:
                    print(f"  {n}: not set in .env, skipped")
                    continue
                S.set_secret(n, value)
                if S.decrypt_check(n, value):
                    S.remove_from_env_file(env_path, n)
                    print(f"  {n}: stored in {S.backend().label}, verified, removed from .env")
                else:
                    print(f"  {n}: stored, but verification failed; left in .env", file=sys.stderr)
                    return 1
            return 0
        if args.secrets_cmd == "delete":
            print("deleted" if S.delete_secret(args.name) else "no such credential")
            return 0
    except S.SecretError as e:
        print(f"secrets: {e}", file=sys.stderr)
        return 2
    return 1
