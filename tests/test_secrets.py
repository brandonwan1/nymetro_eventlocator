import os
import shutil
from pathlib import Path

import pytest

import nymetro_eventlocator.secrets as S
from nymetro_eventlocator.__main__ import main
from nymetro_eventlocator.config import load_config

ROOT = Path(__file__).resolve().parent.parent
HAVE_CREDS = shutil.which("systemd-creds") is not None


class FakeCreds:
    """Stands in for systemd-creds: 'encrypts' by reversing, so no real keys are involved."""

    def __init__(self):
        self.calls = []

    def __call__(self, args, data=None):
        self.calls.append(args)
        if args[2] == "encrypt":
            Path(args[-1]).write_bytes(b"ENC:" + data[::-1])
            return b""
        if args[2] == "decrypt":
            return Path(args[-2]).read_bytes()[4:][::-1]
        raise AssertionError(args)


@pytest.fixture
def fake(monkeypatch):
    f = FakeCreds()
    monkeypatch.setattr(S, "_run", f)
    monkeypatch.setenv("NYMETRO_EVENTLOCATOR_SECRETS_BACKEND", "systemd-creds")
    S.reset()
    return f


class MemoryKeyring:
    """In-memory stand-in for the OS keyring (Windows Credential Manager / macOS Keychain / Secret Service)."""

    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def delete_password(self, service, user):
        del self.store[(service, user)]


@pytest.fixture
def memkeyring(monkeypatch):
    import keyring
    kr = MemoryKeyring()
    monkeypatch.setattr(keyring, "get_password", kr.get_password)
    monkeypatch.setattr(keyring, "set_password", kr.set_password)
    monkeypatch.setattr(keyring, "delete_password", kr.delete_password)
    monkeypatch.setenv("NYMETRO_EVENTLOCATOR_SECRETS_BACKEND", "keyring")
    S.reset()
    return kr


def test_resolution_order(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "from-env")
    assert S.resolve("TICKETMASTER_API_KEY") == "from-env"
    S.set_secret("TICKETMASTER_API_KEY", "from-credential")
    S._cache.clear()
    assert S.resolve("TICKETMASTER_API_KEY") == "from-credential"  # credential beats .env
    handed = tmp_path / "handed"
    handed.mkdir()
    (handed / "ticketmaster_api_key").write_text("from-systemd\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(handed))
    S._cache.clear()
    assert S.resolve("TICKETMASTER_API_KEY") == "from-systemd"  # systemd hand-over beats both


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_file_is_private_and_not_plaintext(fake):
    p = Path(S.set_secret("EVENTBRITE_TOKEN", "token-abc"))
    assert oct(p.stat().st_mode & 0o777) == "0o600" and oct(p.parent.stat().st_mode & 0o777) == "0o700"
    assert b"abc" not in p.read_bytes().replace(b"cba", b"")  # fake cipher: value not stored as-is


def test_config_uses_credentials(fake, monkeypatch):
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    S.set_secret("TICKETMASTER_API_KEY", "k-123")
    S._cache.clear()
    cfg = load_config(ROOT / "config.example.yaml")
    assert cfg.sources["ticketmaster"]["api_key"] == "k-123"


def test_decrypted_once_per_process(fake):
    S.set_secret("X_KEY", "v")
    S._cache.clear()
    for _ in range(3):
        S.resolve("X_KEY")
    assert sum(1 for c in fake.calls if c[2] == "decrypt") == 1


def test_bad_names_and_empty_values(fake):
    with pytest.raises(S.SecretError):
        S.set_secret("../etc/passwd", "x")
    with pytest.raises(S.SecretError):
        S.set_secret("OK_NAME", "   ")


def test_import_env_moves_and_scrubs(fake, tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.yaml"
    cfg.write_text((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    env = tmp_path / ".env"
    env.write_text("TICKETMASTER_API_KEY=secret-1\nOTHER=keep\n", encoding="utf-8")
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    assert main(["-c", str(cfg), "secrets", "import-env"]) == 0
    assert env.read_text(encoding="utf-8") == "OTHER=keep\n"
    S._cache.clear()
    assert S.resolve("TICKETMASTER_API_KEY") == "secret-1"
    out = capsys.readouterr().out
    assert "verified, removed from .env" in out and "secret-1" not in out


def test_list_never_prints_values(fake, tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.yaml"
    cfg.write_text((ROOT / "config.example.yaml").read_text(encoding="utf-8") + "\nother_service:\n  token: ${OTHER_API_TOKEN}\n")
    S.set_secret("TICKETMASTER_API_KEY", "SECRETPART")
    monkeypatch.setenv("EVENTBRITE_TOKEN", "PLAINTEXTKEY")
    assert main(["-c", str(cfg), "secrets", "list"]) == 0
    out = capsys.readouterr().out
    assert "SECRETPART" not in out and "PLAINTEXTKEY" not in out
    assert "TICKETMASTER_API_KEY" in out and "credential (encrypted)" in out and "plaintext" in out
    assert "OTHER_API_TOKEN" in out and "missing" in out


@pytest.mark.skipif(not HAVE_CREDS, reason="systemd-creds not available")
def test_real_systemd_creds_roundtrip(monkeypatch):
    """Real encryption: the file is ciphertext, and decrypts back for this user."""
    monkeypatch.setenv("NYMETRO_EVENTLOCATOR_SECRETS_BACKEND", "systemd-creds")
    S.reset()
    try:
        p = Path(S.set_secret("NYMETRO_EVENTLOCATOR_TEST_PROBE", "roundtrip-value-42"))
    except S.SecretError as e:
        pytest.skip(f"systemd-creds --user unavailable here: {e}")
    assert b"roundtrip-value-42" not in p.read_bytes()
    S._cache.clear()
    assert S.resolve("NYMETRO_EVENTLOCATOR_TEST_PROBE") == "roundtrip-value-42"
    assert S.decrypt_check("NYMETRO_EVENTLOCATOR_TEST_PROBE", "roundtrip-value-42")


def test_referenced_vars_ignores_comments():
    text = "# Secrets come from .env via ${VAR}.\nkey: ${REAL_ONE}   # not ${IN_COMMENT}\nlist: [\"${IN_LIST}\"]\n"
    assert S.referenced_vars(text) == ["IN_LIST", "REAL_ONE"]


def test_keyring_backend_roundtrip(memkeyring, monkeypatch):
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    where = S.set_secret("TICKETMASTER_API_KEY", "kr-secret")
    assert "keyring" in where.lower() and memkeyring.store[("nymetro_eventlocator", "ticketmaster_api_key")] == "kr-secret"
    S._cache.clear()
    assert S.resolve("TICKETMASTER_API_KEY") == "kr-secret"
    assert [st.where for st in S.status(["TICKETMASTER_API_KEY"])][0].startswith("OS keyring")
    assert S.delete_secret("TICKETMASTER_API_KEY") and ("nymetro_eventlocator", "ticketmaster_api_key") not in memkeyring.store


def test_env_backend_is_read_only_and_flagged_plaintext(monkeypatch):
    monkeypatch.setenv("NYMETRO_EVENTLOCATOR_SECRETS_BACKEND", "env")
    monkeypatch.setenv("TICKETMASTER_API_KEY", "plain")
    S.reset()
    assert S.resolve("TICKETMASTER_API_KEY") == "plain"
    assert S.status(["TICKETMASTER_API_KEY"])[0].where.endswith("(plaintext)")
    with pytest.raises(S.SecretError, match="read-only"):
        S.set_secret("TICKETMASTER_API_KEY", "x")


def test_bad_backend_name_is_a_config_error(tmp_path, monkeypatch):
    from nymetro_eventlocator.config import ConfigError
    monkeypatch.delenv("NYMETRO_EVENTLOCATOR_SECRETS_BACKEND", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("secrets_backend: vault\n" + (ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    with pytest.raises(ConfigError, match="secrets_backend must be one of"):
        load_config(cfg)
