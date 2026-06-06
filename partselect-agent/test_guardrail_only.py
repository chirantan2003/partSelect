import asyncio
import os
from dotenv import load_dotenv
load_dotenv(override=True)

from backend.guardrail import is_in_scope

async def test():
    queries = [
        "How can I install part number PS11752778?",
        "Is PS11752778 compatible with my WDT780SAEM1 model?",
        "The ice maker on my Whirlpool fridge is not working. How can I fix it?",
    ]
    for q in queries:
        ans = await is_in_scope(q)
        print(f"Query: {q} -> In-scope: {ans}")

if __name__ == "__main__":
    asyncio.run(test())
