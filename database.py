import os
from datetime import date
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import asyncpg

_pool: Optional[asyncpg.Pool] = None

TOTAL_USAGE_ID = 0  # fila con el total del día; ningún usuario de Telegram tiene ese ID

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Parámetros de las URLs de Neon que asyncpg enviaría al servidor como si fueran ajustes.
CLIENT_ONLY_PARAMS = {"sslmode", "channel_binding"}


def parse_database_url(url: str) -> tuple[str, str | bool]:
    """Separa de la URL lo que asyncpg no entiende. Fuera de la máquina local, siempre con TLS."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    mode = params.get("sslmode", [""])[0]
    query = {k: v for k, v in params.items() if k not in CLIENT_ONLY_PARAMS}
    clean = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    if mode == "disable" and parsed.hostname in LOCAL_HOSTS:
        return clean, False
    if not mode and parsed.hostname in LOCAL_HOSTS:
        return clean, False
    return clean, "require"


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = os.getenv("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL no configurado")
        dsn, ssl = parse_database_url(url)
        _pool = await asyncpg.create_pool(
            dsn, min_size=0, max_size=5, ssl=ssl, command_timeout=15,
            statement_cache_size=0,  # compatible con el pooler de Neon
            max_inactive_connection_lifetime=60,  # Neon cierra las conexiones al dormirse
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                key VARCHAR(255) NOT NULL,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, key)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_user_time
            ON conversations(user_id, created_at DESC)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                day DATE NOT NULL,
                user_id BIGINT NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (day, user_id)
            )
        """)


class _QuotaExceeded(Exception):
    """Interno: deshace la transacción para no gastar el cupo global."""


async def consume_quota(user_id: int, day: date, user_limit: int, total_limit: int) -> str:
    """Descuenta un mensaje de forma atómica. Devuelve 'ok', 'user' o 'total'."""
    query = """INSERT INTO daily_usage (day, user_id, count) VALUES ($1, $2, 1)
               ON CONFLICT (day, user_id) DO UPDATE SET count = daily_usage.count + 1
               WHERE daily_usage.count < $3
               RETURNING count"""
    if user_limit <= 0 or total_limit <= 0:
        return "total"
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                if await conn.fetchval(query, day, TOTAL_USAGE_ID, total_limit) is None:
                    raise _QuotaExceeded("total")
                if await conn.fetchval(query, day, user_id, user_limit) is None:
                    raise _QuotaExceeded("user")
        except _QuotaExceeded as exceeded:
            return exceeded.args[0]
    return "ok"


async def purge_visitor_data(owner_ids: set[int], days: int) -> None:
    """Borra lo que dejaron los visitantes. Los datos de los dueños no vencen."""
    owners = list(owner_ids)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """DELETE FROM conversations
               WHERE created_at < NOW() - make_interval(days => $1) AND NOT (user_id = ANY($2::bigint[]))""",
            days, owners,
        )
        await conn.execute(
            """DELETE FROM memories
               WHERE updated_at < NOW() - make_interval(days => $1) AND NOT (user_id = ANY($2::bigint[]))""",
            days, owners,
        )
        await conn.execute(
            "DELETE FROM daily_usage WHERE day < (NOW() - make_interval(days => $1))::date", days,
        )


async def count_memories(user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM memories WHERE user_id = $1", user_id) or 0


async def save_exchange(user_id: int, user_text: str, reply: str):
    """Guarda el mensaje y su respuesta juntos: nunca queda un mensaje sin responder en el historial."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                "INSERT INTO conversations (user_id, role, content) VALUES ($1, $2, $3)",
                [(user_id, "user", user_text), (user_id, "assistant", reply)],
            )


async def get_history(user_id: int, limit: int = 20) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT role, content FROM (
                SELECT id, role, content FROM conversations
                WHERE user_id = $1
                ORDER BY id DESC LIMIT $2
            ) sub ORDER BY id ASC""",
            user_id, limit
        )
    return list(rows)


async def clear_history(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM conversations WHERE user_id = $1", user_id)


async def clear_memories(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM memories WHERE user_id = $1", user_id)


async def save_memory(user_id: int, key: str, value: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO memories (user_id, key, value)
               VALUES ($1, $2, $3)
               ON CONFLICT (user_id, key)
               DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
            user_id, key, value
        )


async def delete_memory(user_id: int, key: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM memories WHERE user_id = $1 AND key = $2",
            user_id, key
        )
    return result.endswith(" 1")


async def get_memories(user_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM memories WHERE user_id = $1 ORDER BY updated_at DESC",
            user_id
        )
    return {row["key"]: row["value"] for row in rows}


async def get_message_count(user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval(
            "SELECT COUNT(*) FROM conversations WHERE user_id = $1 AND role = 'user'",
            user_id
        )
    return result or 0
