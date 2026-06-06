# backend/agents/orchestrator.py
from langchain.agents import create_agent
from backend.llm import flash
from backend.agents.specialists import catalog_agent_tool, repair_agent_tool, order_agent_tool
from backend.prompts import ORCHESTRATOR_PROMPT

agent_tools = [catalog_agent_tool, repair_agent_tool, order_agent_tool]

orchestrator = create_agent(
    model=flash,
    tools=agent_tools,
    system_prompt=ORCHESTRATOR_PROMPT,
)

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

async def run_orchestrator(user_input: str, context: str = "") -> dict:
    """Run the full agent pipeline. Returns text + tool_results."""
    import json
    
    input_messages = []
    if context:
        for line in context.split("\n"):
            if not line.strip():
                continue
            if line.startswith("user:"):
                input_messages.append({"role": "user", "content": line[5:].strip()})
            elif line.startswith("assistant:"):
                input_messages.append({"role": "assistant", "content": line[10:].strip()})
                
    input_messages.append({"role": "user", "content": user_input})
    
    result = await orchestrator.ainvoke({"messages": input_messages})
    messages = result.get("messages", [])
    
    final_text = ""
    for msg in reversed(messages):
        if msg.__class__.__name__ == "AIMessage" and msg.content:
            final_text = _get_message_text(msg.content)
            break
            
    all_tool_results = []
    agent_name = None
    
    for msg in messages:
        if msg.__class__.__name__ == "AIMessage" and hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") in ["catalog_agent", "repair_agent", "order_agent"]:
                    agent_name = tc.get("name")
                    
    for msg in messages:
        if msg.__class__.__name__ == "ToolMessage":
            try:
                parsed = json.loads(msg.content)
                if isinstance(parsed, dict) and "tool_results" in parsed:
                    all_tool_results.extend(parsed["tool_results"])
            except Exception:
                pass
                
    return {
        "text": final_text,
        "tool_results": all_tool_results,
        "agent": agent_name,
    }
