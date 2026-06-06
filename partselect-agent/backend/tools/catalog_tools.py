# backend/tools/catalog_tools.py
from langchain_core.tools import tool
from backend.db import get_pool
from backend.embeddings import embed

@tool
async def lookup_part(identifier: str) -> dict:
    """Get details for a part by PS number (e.g. PS11752778) or manufacturer
    part number. Returns price, stock, description, rating, appliance_type."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            select * from parts where ps_number = $1
            union all
            select p.* from parts p
            join part_cross_refs cr on cr.part_id = p.id
            where cr.alt_number = $1
            limit 1
        """, identifier)
    if not row:
        return {"error": "part_not_found", "identifier": identifier,
                "hint": "Double-check the number, or try the manufacturer part number."}
    return dict(row)

@tool
async def check_compatibility(ps_number: str, model_number: str) -> dict:
    """Deterministically check if a part fits a specific appliance model.
    Returns a boolean from the compatibility matrix plus both appliance types
    so the agent can explain cross-type mismatches."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        part = await conn.fetchrow(
            "select id, appliance_type from parts where ps_number=$1", ps_number)
        model = await conn.fetchrow(
            "select id, appliance_type from appliance_models where model_number=$1", model_number)
        if not part:
            return {"error": "part_not_found", "ps_number": ps_number}
        if not model:
            return {"error": "model_not_found", "model_number": model_number,
                    "hint": "Model numbers look like WDT780SAEM1. Check the tag inside your appliance door."}
        row = await conn.fetchrow("""
            select exists (
                select 1 from part_compatibility where part_id=$1 and model_id=$2
            ) as compatible
        """, part["id"], model["id"])
    return {
        "compatible": row["compatible"],
        "ps_number": ps_number, "model_number": model_number,
        "part_appliance_type": part["appliance_type"],
        "model_appliance_type": model["appliance_type"],
    }

@tool
async def get_installation_info(ps_number: str) -> dict:
    """Get ordered installation steps, difficulty, and video for a part."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            select s.* from installation_steps s
            join parts p on p.id = s.part_id
            where p.ps_number = $1 order by s.step_no
        """, ps_number)
    if not rows:
        return {"error": "no_install_info", "ps_number": ps_number,
                "hint": "Installation info isn't available for this part yet."}
    return {"ps_number": ps_number, "steps": [dict(r) for r in rows]}

@tool
async def find_parts_by_symptom(symptom: str, appliance_type: str) -> dict:
    """Find parts that fix a specific symptom. Uses BOTH exact symptom matching
    and fuzzy vector search. appliance_type must be 'refrigerator' or 'dishwasher'."""
    vec = await embed(symptom)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exact = await conn.fetch("""
            select p.* from parts p
            join part_symptoms ps on ps.part_id = p.id
            join symptoms s on s.id = ps.symptom_id
            where s.appliance_type = $1 and s.description ilike $2
        """, appliance_type, f"%{symptom}%")
        fuzzy = await conn.fetch("""
            select s.description as matched_symptom, p.*,
                   1 - (s.embedding <=> $1::vector) as score
            from symptoms s
            join part_symptoms ps on ps.symptom_id = s.id
            join parts p on p.id = ps.part_id
            where s.appliance_type = $2
            order by s.embedding <=> $1::vector limit 5
        """, str(vec), appliance_type)
    return {"exact_matches": [dict(r) for r in exact],
            "fuzzy_matches": [dict(r) for r in fuzzy]}
