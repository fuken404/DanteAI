import os

import pytest

import database
from database import parse_database_url


@pytest.mark.parametrize("url,dsn,ssl", [
    ("postgresql://u:c@ep-1.neon.tech/d?sslmode=require&channel_binding=require", "postgresql://u:c@ep-1.neon.tech/d", "require"),
    ("postgresql://u:c@db.example.com/d", "postgresql://u:c@db.example.com/d", "require"),
    ("postgresql://u:c@db.example.com/d?sslmode=disable", "postgresql://u:c@db.example.com/d", "require"),
    ("postgresql://u:c@localhost:5432/d", "postgresql://u:c@localhost:5432/d", False),
    ("postgresql://u:c@127.0.0.1/d?sslmode=disable", "postgresql://u:c@127.0.0.1/d", False),
])
def test_interpreta_la_url(url, dsn, ssl):
    assert parse_database_url(url) == (dsn, ssl)


TEST_URL = os.getenv("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="Requiere TEST_DATABASE_URL")
USER = 987654321


@pytest.fixture
async def db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    database._pool = None
    await database.init_db()
    await database.clear_history(USER)
    await database.clear_memories(USER)
    yield
    await database.clear_history(USER)
    await database.clear_memories(USER)
    await database.close_pool()


@needs_db
async def test_historial_en_orden(db):
    await database.save_exchange(USER, "uno", "dos")
    await database.save_exchange(USER, "tres", "cuatro")
    rows = await database.get_history(USER)
    assert [r["content"] for r in rows] == ["uno", "dos", "tres", "cuatro"]
    assert [r["role"] for r in rows] == ["user", "assistant", "user", "assistant"]
    assert [r["content"] for r in await database.get_history(USER, limit=2)] == ["tres", "cuatro"]
    assert await database.get_message_count(USER) == 2


@needs_db
async def test_recuerdos(db):
    await database.save_memory(USER, "ciudad", "Cali")
    await database.save_memory(USER, "ciudad", "Bogotá")
    assert await database.get_memories(USER) == {"ciudad": "Bogotá"}
    assert await database.delete_memory(USER, "no-existe") is False
    assert await database.delete_memory(USER, "ciudad") is True


@needs_db
async def test_borrar_historial_no_toca_recuerdos(db):
    await database.save_exchange(USER, "a", "b")
    await database.save_memory(USER, "k", "v")
    await database.clear_history(USER)
    assert await database.get_history(USER) == []
    assert await database.get_memories(USER) == {"k": "v"}
