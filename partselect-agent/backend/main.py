# backend/main.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import json
import os
from dotenv import load_dotenv

load_dotenv()

from backend.db import get_pool
from backend.guardrail import is_in_scope
from backend.agents.orchestrator import run_orchestrator
from backend.memory import save_session

app = FastAPI(title="PartSelect Parts Assistant API")

# Configure CORS so Next.js frontend can call it directly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    # Initialize connection pool on startup
    await get_pool()

@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    session_id = body.get("session_id", "default")

    async def event_stream():
        # Get the last user message
        last_user_msg = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )

        # 1) Guardrail Scope Check
        try:
            in_scope = await is_in_scope(last_user_msg)
        except Exception as e:
            # Fallback in case of API errors/rate limits
            print(f"Guardrail error: {e}")
            in_scope = True  # Safe default fallback

        if not in_scope:
            refusal_text = (
                "I am the PartSelect parts assistant. I can help you find refrigerator "
                "and dishwasher parts, check model compatibility, troubleshoot problems, "
                "or track orders. What refrigerator or dishwasher part can I help you find today?"
            )
            yield _sse("text", refusal_text)
            yield _sse("done", {})
            return

        yield _sse("tool_status", {"state": "routing", "message": "Finding the right specialist..."})

        # 2) Build context from recent messages
        context = "\n".join(f"{m['role']}: {m['content']}" for m in messages[-6:-1])

        # 3) Run routing orchestrator
        try:
            result = await run_orchestrator(last_user_msg, context)
        except Exception as e:
            error_message = (
                f"Sorry, I encountered an issue communicating with the AI service: {e}. "
                "This might be due to free-tier API rate limits. Please try again in a few seconds."
            )
            yield _sse("text", error_message)
            yield _sse("done", {})
            return

        # 4) Stream tool results (frontend renders these as cards)
        for tr in result.get("tool_results", []):
            yield _sse("tool_result", tr)

        # 5) Stream assistant text response
        yield _sse("text", result.get("text", ""))
        
        # 6) Signal completion
        yield _sse("done", {"agent": result.get("agent")})

        # 7) Persist session in Supabase/PostgreSQL
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                assistant_msg = {"role": "assistant", "content": result.get("text", "")}
                await save_session(conn, session_id, messages + [assistant_msg])
        except Exception as e:
            print(f"Error saving session: {e}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")

def _sse(event: str, data) -> str:
    payload = json.dumps(data) if isinstance(data, (dict, list)) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
