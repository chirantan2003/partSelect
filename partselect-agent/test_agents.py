# test_agents.py
import asyncio
from dotenv import load_dotenv
load_dotenv()

from backend.db import get_pool, close_pool
from backend.agents.specialists import catalog_executor, repair_executor

def _get_message_text(content) -> str:
    """Extract string content from message content, supporting string or list of dicts."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and "text" in part:
                text_parts.append(part["text"])
            elif isinstance(part, str):
                text_parts.append(part)
        return "".join(text_parts)
    return str(content) if content is not None else ""

async def test_agents():
    pool = await get_pool()

    print("=== Catalog Agent: installation query ===")
    result = await catalog_executor.ainvoke({
        "messages": [{"role": "user", "content": "How can I install part number PS11752778?"}]
    })
    
    # Get last AIMessage
    final_output_raw = next((m.content for m in reversed(result["messages"]) if m.__class__.__name__ == "AIMessage"), "")
    final_output = _get_message_text(final_output_raw)
    print(f"Output: {final_output}")
    print(f"Messages total: {len(result.get('messages', []))}")
    for msg in result["messages"]:
        if msg.__class__.__name__ == "AIMessage" and hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                print(f"  -> {tc['name']}({tc['args']})")

    print("\n=== Catalog Agent: THE TRAP QUERY ===")
    result = await catalog_executor.ainvoke({
        "messages": [{"role": "user", "content": "Is PS11752778 compatible with WDT780SAEM1?"}]
    })
    final_output_raw = next((m.content for m in reversed(result["messages"]) if m.__class__.__name__ == "AIMessage"), "")
    final_output = _get_message_text(final_output_raw)
    print(f"Output: {final_output}")
    
    # Verify the agent mentions the appliance type mismatch
    output = final_output.lower()
    assert "refrigerator" in output or "dishwasher" in output, \
        "Agent should mention the appliance type mismatch!"
    print("Trap query handled correctly by catalog agent.")

    print("\n=== Repair Agent: symptom query ===")
    result = await repair_executor.ainvoke({
        "messages": [{"role": "user", "content": "The ice maker on my Whirlpool fridge is not working. How can I fix it?"}]
    })
    final_output_raw = next((m.content for m in reversed(result["messages"]) if m.__class__.__name__ == "AIMessage"), "")
    final_output = _get_message_text(final_output_raw)
    print(f"Output: {final_output}")

    await close_pool()

if __name__ == "__main__":
    asyncio.run(test_agents())
