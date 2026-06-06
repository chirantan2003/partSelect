# backend/tools/repair_tools.py
from langchain_core.tools import tool
from backend.db import get_pool
from backend.embeddings import embed

@tool
async def search_repair_guides(query: str, appliance_type: str,
                                brand: str = None) -> list[dict]:
    """Semantic search over repair guides and Q&A for troubleshooting advice.
    appliance_type must be 'refrigerator' or 'dishwasher'."""
    vec = await embed(query)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            select id, title, body, source_url, likely_part_ids,
                   1 - (embedding <=> $1::vector) as score
            from repair_guides
            where appliance_type = $2
              and ($3::text is null or brand ilike $3)
            order by embedding <=> $1::vector limit 3
        """, str(vec), appliance_type, brand)
    return [dict(r) for r in rows]
