# backend/guardrail.py
from backend.llm import flash_lite

async def is_in_scope(message: str) -> bool:
    """Classify user query using Gemini 2.5 Flash-Lite. Returns True if in-scope, False otherwise."""
    response = await flash_lite.ainvoke(
        f"""Classify this message. Reply ONLY 'IN' or 'OUT'.
IN = about refrigerator/dishwasher parts, compatibility, installation,
repair for those two types, or PartSelect orders.
OUT = anything else.

Message: {message}"""
    )
    text = response.content.strip()
    return "IN" in text and "OUT" not in text
