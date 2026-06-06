# test_context.py
import asyncio
from dotenv import load_dotenv
load_dotenv(override=True)

from backend.db import get_pool, close_pool
from backend.agents.orchestrator import run_orchestrator

async def test_followup_flow():
    pool = await get_pool()

    # Define the chat context representing the first turn
    context = (
        "user: How can I install part number PS11752778?\n"
        "assistant: To install part number PS11752778, follow these steps: ..."
    )
    
    # Follow-up question referring to "this part"
    query = "Is this part compatible with my WDT780SAEM1 model?"

    print(f"=== Initial Context ===\n{context}\n")
    print(f"=== Follow-up Query: {query} ===")
    
    result = await run_orchestrator(query, context=context)
    
    print(f"\nSelected Specialist: {result['agent']}")
    print(f"Nested Tool calls: {len(result['tool_results'])}")
    for tr in result["tool_results"]:
        print(f"  -> {tr['tool_name']}({tr['args']})")
        
    print(f"\nResponse: {result['text']}")
    
    await close_pool()

if __name__ == "__main__":
    asyncio.run(test_followup_flow())
