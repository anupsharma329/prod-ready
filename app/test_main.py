"""
Tests for our app.

- test_health: /health never touches the database, so it needs zero setup.
- test_ready_without_db: with no database, /ready must say "not ready"
  (503) instead of crashing.
- test_connect_retries_until_database_is_up: the new behaviour. The first
  two connection attempts fail, the third succeeds, and the app must keep
  going until it gets through. Fake objects stand in for Postgres, so this
  runs anywhere, including the CI robot.
"""

import asyncio

from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)


class FakeConn:
    async def execute(self, *args, **kwargs):
        return None


class FakeAcquire:
    async def __aenter__(self):
        return FakeConn()

    async def __aexit__(self, *args):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()

    async def close(self):
        pass


def test_health():
    """/health should always return 200, even with no database at all."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_ready_without_db():
    """/ready should return 503 (not crash!) when no pool was established."""
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not ready"


def test_connect_retries_until_database_is_up(monkeypatch):
    """Two failed attempts, then success: the app must keep retrying."""
    attempts = {"count": 0}
    pauses = []

    async def flaky_create_pool(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise OSError("database not up yet")
        return FakePool()

    async def fake_sleep(seconds):
        pauses.append(seconds)  # record the pause instead of really waiting

    monkeypatch.setattr(main.asyncpg, "create_pool", flaky_create_pool)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "db_pool", None)

    asyncio.run(main.connect_with_retry())

    assert attempts["count"] == 3
    assert main.db_pool is not None
    assert pauses == [1, 2]  # waits grow: 1s, then 2s
