# test_cli.py  — run with: python -m test_cli
import asyncio
from dotenv import load_dotenv
load_dotenv()

from backend.db import get_pool, close_pool
from backend.tools.catalog_tools import lookup_part, check_compatibility

async def test_tools():
    pool = await get_pool()

    print("=== lookup_part ===")
    result = await lookup_part.ainvoke({"identifier": "PS11752778"})
    print(result)

    print("\n=== check_compatibility (THE TRAP — should be False) ===")
    result = await check_compatibility.ainvoke({
        "ps_number": "PS11752778", "model_number": "WDT780SAEM1"
    })
    print(result)
    assert result["compatible"] is False, "TRAP QUERY FAILED!"
    assert result["part_appliance_type"] == "refrigerator"
    assert result["model_appliance_type"] == "dishwasher"
    print("Trap query passed - fridge part vs dishwasher model = incompatible")

    print("\n=== check_compatibility (should be True) ===")
    result = await check_compatibility.ainvoke({
        "ps_number": "PS11752778", "model_number": "WRS322FDAM00"
    })
    print(result)

    await close_pool()

if __name__ == "__main__":
    asyncio.run(test_tools())
