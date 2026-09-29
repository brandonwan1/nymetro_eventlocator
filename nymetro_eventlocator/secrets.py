"""Secrets (API keys and tokens), never in config files, git or logs.

Backends (choose with `secrets_backend:` in config.yaml or the NYMETRO_EVENTLOCATOR_SECRETS_BACKEND env var; default "auto"):
  systemd-creds  Linux: encrypted files in <config dir>/credentials/<name>.cred via `systemd-creds --user`,
                 bound to this machine + user, decrypted in memory only while nymetro_eventlocator runs.
  keyring        The OS secret store: Windows Credential Manager, macOS Keychain, Linux Secret Service
                 (GNOME Keyring / KWallet). Readable by jobs running as the logged-in user.
  env            Environment variables / .env: for Docker and CI. Plaintext; `secrets list` says so.
"auto" = systemd-creds where it works (existing Linux setups), else the OS keyring, else env.

`${VAR}` in config.yaml resolves, in order:
  1. $CREDENTIALS_DIRECTORY/<name>   (credentials handed over by systemd, e.g. LoadCredentialEncrypted= in a system unit)
  2. the chosen backend
  3. the environment / .env          (always the last fallback)
where <name> is VAR in lower case, e.g. TICKETMASTER_API_KEY -> ticketmaster_api_key.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r"^[a-z0-9_]+$")
SERVICE = "nymetro_eventlocator"  # keyring service name
BACKENDS = ("auto", "systemd-creds", "keyring", "env")


class SecretError(RuntimeError):
    pass


def config_dir() -> Path:
    override = os.environ.get("NYMETRO_EVENTLOCATOR_CONFIG_DIR")
    if override:
        return Path(override)
    from platformdirs import user_config_dir

    return Path(user_config_dir("nymetro_eventlocator", appauthor=False))


def cred_dir() -> Path:
    override = os.environ.get("NYMETRO_EVENTLOCATOR_CRED_DIR")
    return Path(override) if override else config_dir() / "credentials"


def cred_name(var: str) -> str:
    name = var.strip().lower()
    if not NAME.match(name):
        raise SecretError(f"invalid secret name {var!r}: use letters, digits and underscores")
    return name


# --- backends ---------------------------------------------------------------------------------


def _run(args: list[str], data: bytes | None = None) -> bytes:
    """Run systemd-creds; separated out so tests can replace it."""
    try:
        proc = subprocess.run(args, input=data, capture_output=True, timeout=30, check=False)
    except FileNotFoundError as e:
        raise SecretError("systemd-creds is not installed") from e
    if proc.returncode != 0:
        raise SecretError(proc.stderr.decode(errors="replace").strip() or f"{args[0]} failed ({proc.returncode})")
    return proc.stdout


class SystemdCreds:
    label = "systemd-creds (encrypted files)"
    plaintext = False

    def path(self, name: str) -> Path:
        return cred_dir() / f"{name}.cred"

    def has(self, name: str) -> bool:
        return self.path(name).is_file()

    def get(self, name: str) -> str | None:
        if not self.has(name):
            return None
        return _run(["systemd-creds", "--user", "decrypt", f"--name={name}", str(self.path(name)), "-"]).decode().strip()

    def set(self, name: str, value: str) -> str:
        d = cred_dir()
        d.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            d.chmod(0o700)
        tmp = d / f".{name}.cred.tmp"
        _run(["systemd-creds", "--user", "encrypt", f"--name={name}", "-", str(tmp)], data=value.encode())
        if os.name == "posix":
            tmp.chmod(0o600)
        tmp.replace(self.path(name))
        return str(self.path(name))

    def delete(self, name: str) -> bool:
        if self.has(name):
            self.path(name).unlink()
            return True
        return False


class Keyring:
    label = "OS keyring"
    plaintext = False

    def __init__(self):
        import keyring

        self.kr = keyring
        self.label = f"OS keyring ({type(keyring.get_keyring()).__name__})"

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def get(self, name: str) -> str | None:
        try:
            return self.kr.get_password(SERVICE, name)
        except Exception as e:  # locked keyring, no D-Bus session, ...
            raise SecretError(f"keyring unavailable: {e}") from e

    def set(self, name: str, value: str) -> str:
        try:
            self.kr.set_password(SERVICE, name, value)
        except Exception as e:
            raise SecretError(f"keyring unavailable: {e}") from e
        return f"{self.label}, service '{SERVICE}', entry '{name}'"

    def delete(self, name: str) -> bool:
        try:
            self.kr.delete_password(SERVICE, name)
            return True
        except Exception:
            return False


class EnvOnly:
    label = "environment / .env (plaintext)"
    plaintext = True

    def has(self, name: str) -> bool:
        return bool(os.environ.get(name.upper()))

    def get(self, name: str) -> str | None:
        return os.environ.get(name.upper()) or None

    def set(self, name: str, value: str) -> str:
        raise SecretError("the 'env' backend is read-only: set the variable in your environment or .env instead")

    def delete(self, name: str) -> bool:
        return False


def _systemd_creds_works() -> bool:
    if os.name != "posix" or shutil.which("systemd-creds") is None:
        return False
    if any(cred_dir().glob("*.cred")):
        return True  # already in use here
    try:
        _run(["systemd-creds", "--user", "encrypt", "--name=nymetro_eventlocator_probe", "-", "-"], data=b"probe")
        return True
    except SecretError:
        return False


def _keyring_works() -> bool:
    try:
        import keyring
        from keyring.backends import fail, null
    except ImportError:
        return False
    return not isinstance(keyring.get_keyring(), (fail.Keyring, null.Keyring))


_preference = "auto"
_backend = None


def configure(preference: str | None) -> None:
    """Called by load_config() with `secrets_backend:` from config.yaml (the env var wins)."""
    global _preference, _backend
    pref = (os.environ.get("NYMETRO_EVENTLOCATOR_SECRETS_BACKEND") or preference or "auto").strip()
    if pref not in BACKENDS:
        raise SecretError(f"secrets_backend must be one of {', '.join(BACKENDS)}, got {pref!r}")
    if pref != _preference:
        _backend = None
        _cache.clear()
    _preference = pref


def backend():
    global _backend
    if _backend is None:
        pref = os.environ.get("NYMETRO_EVENTLOCATOR_SECRETS_BACKEND") or _preference
        if pref == "systemd-creds" or (pref == "auto" and _systemd_creds_works()):
            _backend = SystemdCreds()
        elif pref == "keyring" or (pref == "auto" and _keyring_works()):
            _backend = Keyring()
        else:
            _backend = EnvOnly()
    return _backend


def reset() -> None:
    """Forget the chosen backend and cached values (tests, or after changing settings)."""
    global _backend, _preference
    _backend, _preference = None, "auto"
    _cache.clear()


# --- public API ---------------------------------------------------------------------------------

_cache: dict[str, str] = {}


def resolve(var: str) -> str:
    """The secret's value, or "" if it isn't set anywhere."""
    name = cred_name(var)
    if name in _cache:
        return _cache[name]
    value = ""
    handed = os.environ.get("CREDENTIALS_DIRECTORY")
    if handed and (Path(handed) / name).is_file():
        value = (Path(handed) / name).read_text(encoding="utf-8").strip()
    else:
        value = (backend().get(name) or "").strip() or os.environ.get(var, "")
    _cache[name] = value
    return value


def set_secret(var: str, value: str) -> str:
    """Store a secret in the active backend. Returns where it went (a path or a keyring entry)."""
    name = cred_name(var)
    value = value.strip()
    if not value:
        raise SecretError("empty value; nothing stored")
    where = backend().set(name, value)
    _cache.pop(name, None)
    return where


def decrypt_check(var: str, expected: str) -> bool:
    """Read a freshly stored secret back and compare, before deleting the plaintext copy."""
    name = cred_name(var)
    _cache.pop(name, None)
    return (backend().get(name) or "").strip() == expected.strip()


def delete_secret(var: str) -> bool:
    name = cred_name(var)
    _cache.pop(name, None)
    return backend().delete(name)


@dataclass
class SecretStatus:
    var: str
    where: str


def status(vars_: list[str]) -> list[SecretStatus]:
    b = backend()
    out = []
    for v in vars_:
        if not b.plaintext and b.has(cred_name(v)):
            where = f"{b.label}" if isinstance(b, Keyring) else "credential (encrypted)"
        elif os.environ.get(v):
            where = ".env / environment (plaintext)"
        else:
            where = "missing"
        out.append(SecretStatus(v, where))
    return out


def remove_from_env_file(env_path: Path, var: str) -> bool:
    """Delete VAR=... from .env (after it has been stored in a secure backend)."""
    if not env_path.exists():
        return False
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for line in lines if not re.match(rf"^\s*(export\s+)?{re.escape(var)}\s*=", line)]
    if len(kept) == len(lines):
        return False
    env_path.write_text("".join(kept), encoding="utf-8")
    return True


def referenced_vars(config_text: str) -> list[str]:
    """${VAR} names used in config values (comments are ignored)."""
    import yaml

    found: set[str] = set()

    def walk(v):
        if isinstance(v, str):
            found.update(re.findall(r"\$\{([A-Z0-9_]+)\}", v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(yaml.safe_load(config_text) or {})
    return sorted(found)
