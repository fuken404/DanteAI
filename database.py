import aiosqlite
import os
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "/data/dante.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, key)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_user_time
            ON conversations(user_id, created_at)
        """)
        await db.commit()


async def save_message(user_id: int, role: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content)
        )
        await db.commit()


async def get_history(user_id: int, limit: int = 20) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT role, content FROM (
                SELECT role, content, created_at FROM conversations
                WHERE user_id = ?
                ORDER BY created_at DESC LIMIT ?
            ) ORDER BY created_at ASC""",
            (user_id, limit)
        ) as cursor:
            return await cursor.fetchall()


async def clear_history(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        await db.commit()


async def save_memory(user_id: int, key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO memories (user_id, key, value)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, key)
               DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (user_id, key, value)
        )
        await db.commit()


async def delete_memory(user_id: int, key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM memories WHERE user_id = ? AND key = ?",
            (user_id, key)
        )
        await db.commit()


async def get_memories(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT key, value FROM memories WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
    return {row["key"]: row["value"] for row in rows}


async def get_message_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM conversations WHERE user_id = ? AND role = 'user'",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else 0
