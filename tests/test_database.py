import asyncio
import os
from datetime import date

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


DAY = date(2020, 1, 1)
OTHER = 987654322


@pytest.fixture
async def clean_usage(db):
    pool = await database.get_pool()
    await pool.execute("DELETE FROM daily_usage WHERE day = $1", DAY)
    yield
    await pool.execute("DELETE FROM daily_usage WHERE day = $1", DAY)


@needs_db
async def test_cupo_por_usuario_no_gasta_el_total(clean_usage):
    assert await database.consume_quota(USER, DAY, 2, 10) == "ok"
    assert await database.consume_quota(USER, DAY, 2, 10) == "ok"
    assert await database.consume_quota(USER, DAY, 2, 10) == "user"
    pool = await database.get_pool()
    total = await pool.fetchval(
        "SELECT count FROM daily_usage WHERE day = $1 AND user_id = $2", DAY, database.TOTAL_USAGE_ID,
    )
    assert total == 2  # el intento rechazado se deshizo


@needs_db
async def test_cupo_total(clean_usage):
    assert await database.consume_quota(USER, DAY, 5, 1) == "ok"
    assert await database.consume_quota(OTHER, DAY, 5, 1) == "total"


@needs_db
async def test_cupo_aguanta_mensajes_simultaneos(clean_usage):
    results = await asyncio.gather(*(database.consume_quota(USER, DAY, 3, 100) for _ in range(5)))
    assert sorted(results) == ["ok", "ok", "ok", "user", "user"]


@needs_db
async def test_cupo_en_cero_cierra_la_demo(clean_usage):
    assert await database.consume_quota(USER, DAY, 0, 10) == "total"


@needs_db
async def test_borra_datos_viejos_solo_de_visitantes(clean_usage):
    pool = await database.get_pool()
    for user in (USER, OTHER):
        await database.save_exchange(user, "viejo", "viejo")
        await database.save_memory(user, "k", "v")
    await pool.execute("UPDATE conversations SET created_at = NOW() - INTERVAL '30 days' WHERE user_id = ANY($1::bigint[])", [USER, OTHER])
    await pool.execute("UPDATE memories SET updated_at = NOW() - INTERVAL '30 days' WHERE user_id = ANY($1::bigint[])", [USER, OTHER])

    await database.purge_visitor_data({USER}, 7)

    assert len(await database.get_history(USER)) == 2 and await database.get_memories(USER) == {"k": "v"}
    assert await database.get_history(OTHER) == [] and await database.get_memories(OTHER) == {}


@needs_db
async def test_borrar_historial_no_toca_recuerdos(db):
    await database.save_exchange(USER, "a", "b")
    await database.save_memory(USER, "k", "v")
    await database.clear_history(USER)
    assert await database.get_history(USER) == []
    assert await database.get_memories(USER) == {"k": "v"}
