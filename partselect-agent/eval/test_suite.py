# eval/test_suite.py
from dotenv import load_dotenv
load_dotenv()

import pytest
from backend.tools.catalog_tools import lookup_part, check_compatibility
from backend.guardrail import is_in_scope

@pytest.mark.asyncio
async def test_trap_query():
    """PS11752778 (fridge) vs WDT780SAEM1 (dishwasher) -> incompatible."""
    result = await check_compatibility.ainvoke({
        "ps_number": "PS11752778", "model_number": "WDT780SAEM1"})
    assert result["compatible"] is False
    assert result["part_appliance_type"] == "refrigerator"
    assert result["model_appliance_type"] == "dishwasher"

@pytest.mark.asyncio
async def test_compatible():
    result = await check_compatibility.ainvoke({
        "ps_number": "PS11752778", "model_number": "WRS322FDAM00"})
    assert result["compatible"] is True

@pytest.mark.asyncio
async def test_part_not_found():
    result = await lookup_part.ainvoke({"identifier": "PS99999999"})
    assert "error" in result
    assert result["error"] == "part_not_found"

@pytest.mark.asyncio
async def test_guardrail_in():
    assert await is_in_scope("Is PS11752778 compatible with my fridge?") == True

@pytest.mark.asyncio
async def test_guardrail_out():
    assert await is_in_scope("Write me a poem about cats") == False

@pytest.mark.asyncio
async def test_guardrail_washer_out_of_scope():
    assert await is_in_scope("Help me fix my washing machine") == False
