# backend/guardrail.py
from backend.llm import flash_lite

async def is_in_scope(message: str, context: str = "") -> bool:
    """Classify user query using Gemini 2.5 Flash-Lite. Returns True if in-scope, False otherwise."""
    prompt = f"""Classify this message. Reply ONLY 'IN' or 'OUT'.
IN = about refrigerator/dishwasher appliances, models (e.g. WRS322FDAM00), parts, compatibility, installation,
repair/troubleshooting for refrigerators and dishwashers, or PartSelect orders. Also includes brief answers/clarifications (like part numbers, model numbers, yes/no) to prior assistant questions in context.
OUT = anything else.

"""
    if context:
        prompt += f"Conversation context:\n{context}\n\n"
    prompt += f"Message to classify: {message}"

    response = await flash_lite.ainvoke(prompt)
    text = response.content.strip()
    return "IN" in text and "OUT" not in text
