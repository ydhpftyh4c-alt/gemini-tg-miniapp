import aiosqlite
import os
from typing import List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "chat_history.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                type TEXT DEFAULT 'chat',
                media_url TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_id ON messages(user_id)
        """)
        # Safe migration if columns were missing
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN type TEXT DEFAULT 'chat'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN media_url TEXT DEFAULT ''")
        except Exception:
            pass
        await db.commit()

async def add_message(user_id: int, role: str, content: str, msg_type: str = "chat", media_url: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (user_id, role, content, type, media_url) VALUES (?, ?, ?, ?, ?)",
            (user_id, role, content, msg_type, media_url)
        )
        await db.commit()

async def get_history_for_gemini(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Returns chronological messages in Gemini format"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            ordered = list(reversed(rows))
            history = []
            for r in ordered:
                history.append({
                    "role": r["role"],
                    "parts": [{"text": r["content"]}]
                })
            return history

async def get_ui_history(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """Returns messages formatted for the UI drawer/history view"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, role, content, type, media_url, created_at FROM messages WHERE user_id = ? ORDER BY id ASC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def clear_history(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        await db.commit()
