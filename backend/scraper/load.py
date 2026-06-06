# backend/scraper/load.py
"""
Database loading functions for scraped PartSelect data.

Supports both individual part loading (legacy) and batch loading
from scraped JSON files.
"""

import asyncio
import random
import asyncpg
from backend.scraper.extract import ScrapedPart, ScrapedModel
from backend.embeddings import embed

# ─── Legacy Individual Loaders (kept for backward compat) ─────────────

async def load_part(conn: asyncpg.Connection, part: ScrapedPart):
    """Load a single ScrapedPart into the database."""
    row = await conn.fetchrow("""
        insert into parts (ps_number, mpn, name, description, price_cents,
                           in_stock, image_url, rating, review_count, appliance_type, video_url)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        on conflict (ps_number) do update set 
            price_cents=excluded.price_cents, 
            in_stock=excluded.in_stock,
            rating=excluded.rating,
            review_count=excluded.review_count,
            description=excluded.description,
            image_url=excluded.image_url
        returning id
    """, part.ps_number, part.mpn, part.name, part.description, part.price_cents,
        part.in_stock, part.image_url, part.rating, part.review_count,
        part.appliance_type, part.video_url)
    part_id = row["id"]

    for alt in part.alt_numbers:
        await conn.execute("""
            insert into part_cross_refs (part_id, alt_number) values ($1, $2)
            on conflict do nothing
        """, part_id, alt)

    for desc in part.symptoms:
        symptom_row = await conn.fetchrow("select id from symptoms where description=$1", desc)
        if symptom_row:
            symptom_id = symptom_row["id"]
        else:
            vec = await embed(desc)
            symptom_row = await conn.fetchrow("""
                insert into symptoms (description, appliance_type, embedding)
                values ($1, $2, $3)
                on conflict (description) do nothing returning id
            """, desc, part.appliance_type, str(vec))
            if symptom_row:
                symptom_id = symptom_row["id"]
            else:
                symptom_row = await conn.fetchrow("select id from symptoms where description=$1", desc)
                symptom_id = symptom_row["id"]
        
        await conn.execute("""
            insert into part_symptoms (part_id, symptom_id) values ($1, $2)
            on conflict do nothing
        """, part_id, symptom_id)

    for step in part.install_steps:
        await conn.execute("""
            insert into installation_steps (part_id, step_no, text, difficulty, est_minutes, video_url)
            values ($1, $2, $3, $4, $5, $6)
        """, part_id, step["step_no"], step["text"], step.get("difficulty"), step.get("est_minutes"), step.get("video_url"))


async def load_model(conn: asyncpg.Connection, model: ScrapedModel):
    """Load a single ScrapedModel into the database."""
    row = await conn.fetchrow("""
        insert into appliance_models (model_number, brand, appliance_type)
        values ($1, $2, $3) on conflict (model_number) do nothing returning id
    """, model.model_number, model.brand, model.appliance_type)
    
    if row:
        model_id = row["id"]
    else:
        model_row = await conn.fetchrow("select id from appliance_models where model_number=$1", model.model_number)
        model_id = model_row["id"]

    for ps in model.part_ps_numbers:
        await conn.execute("""
            insert into part_compatibility (part_id, model_id)
            select p.id, $2 from parts p where p.ps_number = $1
            on conflict do nothing
        """, ps, model_id)


# ─── Batch Loaders (for scraped JSON data) ────────────────────────────

async def load_parts_batch(conn: asyncpg.Connection, parts: list[dict], batch_size: int = 100):
    """
    Bulk-load parts from scraped JSON documents.
    
    Args:
        conn: Database connection
        parts: List of part dicts from parts_raw.json
        batch_size: Number of parts per transaction batch
    
    Returns:
        dict with loaded count, skipped count, errors
    """
    loaded = 0
    skipped = 0
    errors = []

    for i in range(0, len(parts), batch_size):
        batch = parts[i:i + batch_size]
        
        async with conn.transaction():
            for part_doc in batch:
                try:
                    ps = part_doc.get("ps_number", "")
                    if not ps:
                        skipped += 1
                        continue

                    name = part_doc.get("name", "Unknown Part")
                    appliance_type = part_doc.get("appliance_type", "").lower()
                    
                    if appliance_type not in ("refrigerator", "dishwasher"):
                        skipped += 1
                        continue

                    row = await conn.fetchrow("""
                        insert into parts (ps_number, mpn, name, description, price_cents,
                                           in_stock, image_url, rating, review_count, 
                                           appliance_type, video_url)
                        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                        on conflict (ps_number) do update set 
                            mpn = coalesce(nullif(excluded.mpn, ''), parts.mpn),
                            name = coalesce(nullif(excluded.name, ''), parts.name),
                            price_cents = case when excluded.price_cents > 0 
                                              then excluded.price_cents else parts.price_cents end,
                            in_stock = excluded.in_stock,
                            rating = coalesce(excluded.rating, parts.rating),
                            review_count = greatest(excluded.review_count, parts.review_count),
                            description = coalesce(nullif(excluded.description, ''), parts.description),
                            image_url = coalesce(nullif(excluded.image_url, ''), parts.image_url),
                            video_url = coalesce(nullif(excluded.video_url, ''), parts.video_url)
                        returning id
                    """,
                        ps,
                        part_doc.get("mpn", ""),
                        name,
                        part_doc.get("description", ""),
                        part_doc.get("price_cents", 0),
                        part_doc.get("in_stock", True),
                        part_doc.get("image_url"),
                        part_doc.get("rating"),
                        part_doc.get("review_count", 0),
                        appliance_type,
                        part_doc.get("video_url"),
                    )
                    part_id = row["id"]

                    # Cross-reference numbers
                    for alt in part_doc.get("alt_numbers", []):
                        if alt:
                            await conn.execute("""
                                insert into part_cross_refs (part_id, alt_number) values ($1, $2)
                                on conflict do nothing
                            """, part_id, alt)

                    loaded += 1

                except Exception as e:
                    errors.append({"ps_number": part_doc.get("ps_number"), "error": str(e)})

        print(f"  Loaded batch {i//batch_size + 1}: {loaded} parts total", end="\r", flush=True)

    print()  # Newline after progress
    return {"loaded": loaded, "skipped": skipped, "errors": errors}


async def load_symptoms_batch(
    conn: asyncpg.Connection,
    symptoms: list[dict],
    embeddings: dict[str, list[float]] | None = None,
):
    """
    Bulk-load symptoms with pre-computed embeddings.
    
    Args:
        conn: Database connection
        symptoms: List of {description, appliance_type} dicts
        embeddings: Optional pre-computed {description: vector} map
    """
    loaded = 0
    
    # Cache to avoid duplicate inserts
    existing = {}
    rows = await conn.fetch("select id, description from symptoms")
    for r in rows:
        existing[r["description"]] = r["id"]

    for sym in symptoms:
        desc = sym.get("description", "").strip()
        app_type = sym.get("appliance_type", "")
        
        if not desc or desc in existing:
            continue

        # Get embedding
        vec = None
        if embeddings and desc in embeddings:
            vec = embeddings[desc]
        else:
            try:
                vec = await embed(desc)
            except Exception as e:
                print(f"  [Warning] Embedding failed for '{desc}': {e}")
                vec = [random.uniform(-0.1, 0.1) for _ in range(768)]

        row = await conn.fetchrow("""
            insert into symptoms (description, appliance_type, embedding)
            values ($1, $2, $3)
            on conflict (description) do nothing returning id
        """, desc, app_type, str(vec))

        s_id = row["id"] if row else (await conn.fetchrow(
            "select id from symptoms where description=$1", desc
        ))["id"]
        existing[desc] = s_id
        loaded += 1

    return {"loaded": loaded, "cache": existing}


async def link_parts_symptoms(
    conn: asyncpg.Connection,
    part_symptom_pairs: list[tuple[str, str]],
    symptom_cache: dict[str, int],
):
    """
    Link parts to symptoms in the part_symptoms join table.
    
    Args:
        part_symptom_pairs: List of (ps_number, symptom_description) tuples
        symptom_cache: {description: symptom_id} mapping
    """
    linked = 0
    for ps_number, symptom_desc in part_symptom_pairs:
        symptom_id = symptom_cache.get(symptom_desc)
        if not symptom_id:
            continue

        part_row = await conn.fetchrow(
            "select id from parts where ps_number=$1", ps_number
        )
        if not part_row:
            continue

        await conn.execute("""
            insert into part_symptoms (part_id, symptom_id) values ($1, $2)
            on conflict do nothing
        """, part_row["id"], symptom_id)
        linked += 1

    return linked


async def load_repair_guides_batch(
    conn: asyncpg.Connection,
    guides: list[dict],
):
    """
    Bulk-load repair guides from scraped repair symptom data.
    
    Args:
        guides: List of repair guide dicts from repair_symptoms_raw.json
    """
    loaded = 0

    for guide in guides:
        appliance_type = guide.get("appliance_type", "")
        title = guide.get("title", "")
        body = guide.get("repair_story", "")
        source_url = guide.get("source_url", "")

        if not body or len(body) < 50:
            continue

        # Get related part IDs
        likely_part_ids = []
        for rp in guide.get("related_parts", []):
            ps = rp.get("ps_number")
            if ps:
                row = await conn.fetchrow("select id from parts where ps_number=$1", ps)
                if row:
                    likely_part_ids.append(row["id"])

        # Embed the guide body
        try:
            vec = await embed(body[:2000])  # Cap embedding input
        except Exception as e:
            print(f"  [Warning] Embedding failed for guide '{title}': {e}")
            vec = [random.uniform(-0.1, 0.1) for _ in range(768)]

        await conn.execute("""
            insert into repair_guides (appliance_type, brand, title, body, source_url, 
                                       likely_part_ids, embedding)
            values ($1, $2, $3, $4, $5, $6, $7)
        """, appliance_type, None, title, body, source_url, likely_part_ids, str(vec))

        loaded += 1

    return loaded


async def build_compatibility_matrix(conn: asyncpg.Connection):
    """
    Build the part_compatibility table by matching appliance types.
    All fridge parts are compatible with fridge models, etc.
    """
    models = await conn.fetch("select id, appliance_type from appliance_models")
    
    for model in models:
        await conn.execute("""
            insert into part_compatibility (part_id, model_id)
            select p.id, $1 from parts p where p.appliance_type = $2
            on conflict do nothing
        """, model["id"], model["appliance_type"])

    count = await conn.fetchval("select count(*) from part_compatibility")
    return count
