# test_orchestrator.py
import asyncio
from dotenv import load_dotenv
load_dotenv()

from backend.db import get_pool, close_pool
from backend.agents.orchestrator import run_orchestrator
from backend.guardrail import is_in_scope

async def test_full_flow():
    pool = await get_pool()

    # Test guardrail
    print("=== Guardrail ===")
    
    in_scope_1 = await is_in_scope("How can I install PS11752778?")
    print(f"'How can I install PS11752778?' -> In-scope: {in_scope_1}")
    assert in_scope_1 == True
    
    in_scope_2 = await is_in_scope("Write me a poem about cats")
    print(f"'Write me a poem about cats' -> In-scope: {in_scope_2}")
    assert in_scope_2 == False
    
    in_scope_3 = await is_in_scope("Help me fix my washing machine")
    print(f"'Help me fix my washing machine' -> In-scope: {in_scope_3}")
    assert in_scope_3 == False  # out of scope: washers
    
    print("Guardrail checks passed.")

    # Test full orchestrator with all 3 example queries
    queries = [
        "How can I install part number PS11752778?",
        "Is PS11752778 compatible with my WDT780SAEM1 model?",
        "The ice maker on my Whirlpool fridge is not working. How can I fix it?",
    ]

    for q in queries:
        print(f"\n=== Query: {q} ===")
        if not await is_in_scope(q):
            print("  OUT OF SCOPE (unexpected!)")
            continue
        result = await run_orchestrator(q)
        print(f"  Selected Specialist: {result['agent']}")
        print(f"  Nested Tool calls: {len(result['tool_results'])}")
        for tr in result["tool_results"]:
            print(f"    -> {tr['tool_name']}({tr['args']})")
        
        # Print first few characters of output
        preview = result["text"][:250].replace("\n", " ")
        print(f"  Response: {preview}...")

    await close_pool()

if __name__ == "__main__":
    asyncio.run(test_full_flow())
