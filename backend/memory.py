# backend/memory.py
import json

async def load_session(conn, session_id: str) -> list[dict]:
    """Retrieve chat history messages from database for a session ID."""
    row = await conn.fetchrow(
        "select messages from chat_sessions where session_id=$1", session_id)
    return json.loads(row["messages"]) if row else []

async def save_session(conn, session_id: str, messages: list[dict]):
    """Insert or update chat history messages for a session ID."""
    await conn.execute("""
        insert into chat_sessions (session_id, messages, updated_at)
        values ($1, $2, now())
        on conflict (session_id) do update
          set messages = excluded.messages, updated_at = now()
    """, session_id, json.dumps(messages))
