from datetime import datetime, timedelta, UTC

import pytest

from nymetro_eventlocator.db.store import Store
from nymetro_eventlocator.models import Event


@pytest.fixture(autouse=True)
def isolated_secrets(tmp_path, monkeypatch):
    """Tests never read the real encrypted credentials."""
    import nymetro_eventlocator.secrets as secrets

    monkeypatch.setenv("NYMETRO_EVENTLOCATOR_CRED_DIR", str(tmp_path / "creds"))
    monkeypatch.setenv("NYMETRO_EVENTLOCATOR_SECRETS_BACKEND", "env")  # never probe the real keyring / systemd-creds
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    secrets.reset()
    yield
    secrets.reset()


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def make_event():
    def _make(title="Test event", source="test", source_id=None, start=None, **kw):
        start = start or datetime.now(UTC) + timedelta(days=3)
        return Event(source=source, source_id=source_id or title, title=title, start=start,
                     url=kw.pop("url", f"https://example.test/{(source_id or title).replace(' ', '-')}"), **kw)
    return _make
