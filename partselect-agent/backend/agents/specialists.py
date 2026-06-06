# backend/agents/specialists.py
import json
import ast
from langchain.agents import create_agent
from langchain_core.tools import Tool
from backend.llm import flash
from backend.tools.catalog_tools import (
    lookup_part, check_compatibility, get_installation_info, find_parts_by_symptom,
)
from backend.tools.repair_tools import search_repair_guides
from backend.tools.order_tools import add_to_cart, get_order_status
from backend.prompts import CATALOG_AGENT_PROMPT, REPAIR_AGENT_PROMPT, ORDER_AGENT_PROMPT

# ── Specialist executors ──────────────────────────────────────────
catalog_executor = create_agent(
    model=flash,
    tools=[lookup_part, check_compatibility, get_installation_info, find_parts_by_symptom],
    system_prompt=CATALOG_AGENT_PROMPT,
)

repair_executor = create_agent(
    model=flash,
    tools=[search_repair_guides, find_parts_by_symptom, lookup_part],
    system_prompt=REPAIR_AGENT_PROMPT,
)

order_executor = create_agent(
    model=flash,
    tools=[add_to_cart, get_order_status, lookup_part],
    system_prompt=ORDER_AGENT_PROMPT,
)

# ── Wrap each executor as a Tool for the orchestrator ─────────────
async def _run_catalog(query: str) -> str:
    result = await catalog_executor.ainvoke({"messages": [{"role": "user", "content": query}]})
    return _format_agent_result(result)

async def _run_repair(query: str) -> str:
    result = await repair_executor.ainvoke({"messages": [{"role": "user", "content": query}]})
    return _format_agent_result(result)

async def _run_order(query: str) -> str:
    result = await order_executor.ainvoke({"messages": [{"role": "user", "content": query}]})
    return _format_agent_result(result)

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

def _format_agent_result(result: dict) -> str:
    """Combine text + tool results into a structured JSON string the
    orchestrator and frontend can parse."""
    messages = result.get("messages", [])
    
    # Find the final text response (the last AIMessage with non-empty content)
    final_text = ""
    for msg in reversed(messages):
        if msg.__class__.__name__ == "AIMessage" and msg.content:
            final_text = _get_message_text(msg.content)
            break
            
    # Build a lookup map of ToolMessages by tool_call_id
    tool_msgs = {}
    for msg in messages:
        if msg.__class__.__name__ == "ToolMessage":
            tool_msgs[msg.tool_call_id] = msg.content

    # Match tool calls to their execution results
    tool_results = []
    for msg in messages:
        if msg.__class__.__name__ == "AIMessage" and hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tc_id = tc.get("id")
                raw_result = tool_msgs.get(tc_id, "")
                
                # Parse output to dict/list if possible
                obs_data = raw_result
                if isinstance(raw_result, str):
                    try:
                        obs_data = json.loads(raw_result)
                    except json.JSONDecodeError:
                        try:
                            obs_data = ast.literal_eval(raw_result)
                        except Exception:
                            obs_data = raw_result
                            
                tool_results.append({
                    "tool_name": tc.get("name"),
                    "args": tc.get("args"),
                    "result": obs_data,
                })
                
    return json.dumps({
        "text": final_text,
        "tool_results": tool_results,
    })

catalog_agent_tool = Tool(
    name="catalog_agent",
    description=(
        "Handles part lookups, compatibility checks, installation info, and finding "
        "parts by symptom. Use for: 'find part X', 'is X compatible with Y', "
        "'how to install X', 'what part fixes [symptom]'."
    ),
    coroutine=_run_catalog,
    func=lambda q: None,   # async-only
)

repair_agent_tool = Tool(
    name="repair_agent",
    description=(
        "Handles troubleshooting and repair guidance. Use for: 'my fridge is leaking', "
        "'ice maker not working', 'how to fix X'."
    ),
    coroutine=_run_repair,
    func=lambda q: None,
)

order_agent_tool = Tool(
    name="order_agent",
    description=(
        "Handles cart operations and order tracking. Use for: 'add to cart', "
        "'where is my order', 'check order ORD-XXX'."
    ),
    coroutine=_run_order,
    func=lambda q: None,
)
