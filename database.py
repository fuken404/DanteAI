import asyncpg
import os
from typing import Optional

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        database_url = os.getenv("DATABASE_URL", "")
        # asyncpg no soporta sslmode en el DSN — lo extraemos y pasamos ssl por separado
        if "sslmode=" in database_url:
            from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
            parsed = urlparse(database_url)
            query = {k: v for k, v in parse_qs(parsed.query).items() if k != "sslmode"}
            database_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5, ssl="require")
    return _pool


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


async def save_message(user_id: int, role: str, content: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO conversations (user_id, role, content) VALUES ($1, $2, $3)",
            user_id, role, content
        )


async def get_history(user_id: int, limit: int = 20) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT role, content FROM (
                SELECT role, content, created_at FROM conversations
                WHERE user_id = $1
                ORDER BY created_at DESC LIMIT $2
            ) sub ORDER BY created_at ASC""",
            user_id, limit
        )
    return list(rows)


async def clear_history(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM conversations WHERE user_id = $1", user_id)


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


async def delete_memory(user_id: int, key: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM memories WHERE user_id = $1 AND key = $2",
            user_id, key
        )


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
