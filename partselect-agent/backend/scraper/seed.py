# backend/scraper/seed.py
"""
Database seeder for PartSelect agent.

Two modes:
  1) --from-json : Load scraped data from data/raw/*.json files
  2) (default)   : Use hardcoded 60-part seed for development/testing

Usage:
    python -m backend.scraper.seed                  # hardcoded 60 parts
    python -m backend.scraper.seed --from-json       # from scraped JSON files
    python -m backend.scraper.seed --from-json --reset  # truncate first, then load
"""

import asyncio
import os
import sys
import json
import random
import argparse
from dotenv import load_dotenv

load_dotenv()

from backend.db import get_pool, close_pool
from backend.embeddings import embed
from backend.scraper.load import (
    load_part, load_model, load_parts_batch, load_symptoms_batch,
    link_parts_symptoms, load_repair_guides_batch, build_compatibility_matrix,
)
from backend.scraper.extract import ScrapedPart, ScrapedModel
from backend.scraper.utils import load_json, get_data_dir


# ─── JSON-based loading (from scraped data) ──────────────────────────

async def seed_from_json(reset: bool = False):
    """Load database from scraped JSON files in data/raw/."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        data_dir = get_data_dir()

        if reset:
            print("Resetting database tables...")
            await _truncate_all(conn)

        # ── 1) Load parts ────────────────────────────────────────
        parts_file = os.path.join(data_dir, "parts_raw.json")
        if os.path.exists(parts_file):
            print(f"\nLoading parts from {parts_file}...")
            with open(parts_file, "r", encoding="utf-8") as f:
                parts_data = json.load(f)
            
            documents = parts_data.get("documents", [])
            print(f"  Found {len(documents)} parts in JSON")
            
            result = await load_parts_batch(conn, documents)
            print(f"  Loaded: {result['loaded']}, Skipped: {result['skipped']}, Errors: {len(result['errors'])}")
            
            if result["errors"]:
                for err in result["errors"][:5]:
                    print(f"    Error: {err['ps_number']} -> {err['error']}")
        else:
            print(f"  [Skip] {parts_file} not found. Run parts_scraper first.")

        # ── 2) Load symptoms from repair data ────────────────────
        repair_file = os.path.join(data_dir, "repair_symptoms_raw.json")
        all_symptoms = []
        part_symptom_pairs = []

        if os.path.exists(repair_file):
            print(f"\nLoading repair symptoms from {repair_file}...")
            with open(repair_file, "r", encoding="utf-8") as f:
                repair_data = json.load(f)
            
            for doc in repair_data.get("documents", []):
                app_type = doc.get("appliance_type", "")
                symptom_name = doc.get("symptom_name", "")
                
                if symptom_name:
                    all_symptoms.append({
                        "description": symptom_name,
                        "appliance_type": app_type,
                    })
                
                # Link related parts to this symptom
                for rp in doc.get("related_parts", []):
                    ps = rp.get("ps_number")
                    if ps and symptom_name:
                        part_symptom_pairs.append((ps, symptom_name))
            
            print(f"  Found {len(all_symptoms)} unique symptoms, {len(part_symptom_pairs)} part-symptom links")
        
        # Also extract symptoms from parts data if available
        if os.path.exists(parts_file):
            with open(parts_file, "r", encoding="utf-8") as f:
                parts_data = json.load(f)
            for doc in parts_data.get("documents", []):
                ps = doc.get("ps_number")
                app_type = doc.get("appliance_type", "")
                for sym in doc.get("symptoms", []):
                    if sym:
                        all_symptoms.append({
                            "description": sym,
                            "appliance_type": app_type,
                        })
                        if ps:
                            part_symptom_pairs.append((ps, sym))

        # Deduplicate symptoms
        seen = set()
        unique_symptoms = []
        for s in all_symptoms:
            if s["description"] not in seen:
                seen.add(s["description"])
                unique_symptoms.append(s)
        
        if unique_symptoms:
            print(f"\nEmbedding and loading {len(unique_symptoms)} symptoms...")
            sym_result = await load_symptoms_batch(conn, unique_symptoms)
            print(f"  Loaded: {sym_result['loaded']} symptoms")
            
            # Link parts to symptoms
            if part_symptom_pairs:
                print(f"  Linking {len(part_symptom_pairs)} part-symptom pairs...")
                linked = await link_parts_symptoms(conn, part_symptom_pairs, sym_result["cache"])
                print(f"  Linked: {linked} pairs")

        # ── 3) Load repair guides ────────────────────────────────
        if os.path.exists(repair_file):
            print(f"\nLoading repair guides...")
            with open(repair_file, "r", encoding="utf-8") as f:
                repair_data = json.load(f)
            guides = repair_data.get("documents", [])
            loaded_guides = await load_repair_guides_batch(conn, guides)
            print(f"  Loaded: {loaded_guides} repair guides")

        # ── 4) Load blog articles as additional repair guides ────
        blog_file = os.path.join(data_dir, "blogs_raw.json")
        if os.path.exists(blog_file):
            print(f"\nLoading blog articles as repair guides from {blog_file}...")
            with open(blog_file, "r", encoding="utf-8") as f:
                blog_data = json.load(f)
            
            blog_guides = []
            for doc in blog_data.get("documents", []):
                if doc.get("content_text") and len(doc["content_text"]) > 100:
                    blog_guides.append({
                        "appliance_type": doc.get("appliance_type", ""),
                        "title": doc.get("title", ""),
                        "repair_story": doc.get("content_text", ""),
                        "source_url": doc.get("source_url", ""),
                        "related_parts": [
                            {"ps_number": ps} for ps in doc.get("ps_numbers", [])
                        ],
                    })
            
            if blog_guides:
                loaded_blogs = await load_repair_guides_batch(conn, blog_guides)
                print(f"  Loaded: {loaded_blogs} blog-based guides")
        else:
            print(f"  [Skip] {blog_file} not found.")

        # ── 5) Ensure default appliance models exist ─────────────
        print(f"\nEnsuring appliance models...")
        default_models = [
            ScrapedModel("WRS322FDAM00", "Whirlpool", "refrigerator", []),
            ScrapedModel("WRF535SWHZ", "Whirlpool", "refrigerator", []),
            ScrapedModel("WDT780SAEM1", "Whirlpool", "dishwasher", []),
            ScrapedModel("WDF520PADM", "Whirlpool", "dishwasher", []),
        ]
        for m in default_models:
            await load_model(conn, m)
            print(f"  Model {m.model_number} ensured.")

        # ── 6) Build compatibility matrix ─────────────────────────
        print(f"\nBuilding compatibility matrix...")
        compat_count = await build_compatibility_matrix(conn)
        print(f"  {compat_count} compatibility links")

        # ── 7) Ensure test order exists ───────────────────────────
        print(f"\nEnsuring test order...")
        await conn.execute("""
            insert into orders (id, session_id, status, tracking_url)
            values ('ORD-10293', 'test-session', 'Shipped', 'https://www.partselect.com/tracking/ORD-10293')
            on conflict do nothing
        """)

        # ── Summary ──────────────────────────────────────────────
        part_count = await conn.fetchval("select count(*) from parts")
        symptom_count = await conn.fetchval("select count(*) from symptoms")
        guide_count = await conn.fetchval("select count(*) from repair_guides")
        compat_count = await conn.fetchval("select count(*) from part_compatibility")

        print(f"\n{'='*50}")
        print(f"  Database Summary:")
        print(f"  Parts:          {part_count}")
        print(f"  Symptoms:       {symptom_count}")
        print(f"  Repair Guides:  {guide_count}")
        print(f"  Compat Links:   {compat_count}")
        print(f"{'='*50}")

    await close_pool()


# ─── Legacy hardcoded seed (for dev/testing) ──────────────────────────

async def seed_data():
    """Original 60-part hardcoded seed for development/testing."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _truncate_all(conn)

        print("Seeding appliance models...")
        models = [
            ScrapedModel("WRS322FDAM00", "Whirlpool", "refrigerator", []),
            ScrapedModel("WRF535SWHZ", "Whirlpool", "refrigerator", []),
            ScrapedModel("WDT780SAEM1", "Whirlpool", "dishwasher", []),
            ScrapedModel("WDF520PADM", "Whirlpool", "dishwasher", [])
        ]
        
        for m in models:
            await load_model(conn, m)
            print(f"  Model {m.model_number} loaded.")

        symptom_id_cache = {}
        async def get_or_create_symptom(desc, app_type):
            if desc in symptom_id_cache:
                return symptom_id_cache[desc]
            row = await conn.fetchrow("select id from symptoms where description=$1", desc)
            if row:
                symptom_id_cache[desc] = row["id"]
                return row["id"]
            
            print(f"  [API] Embedding symptom: '{desc}'")
            try:
                vec = await embed(desc)
            except Exception as e:
                print(f"  [Warning] API call failed: {e}. Using mock embedding.")
                vec = [random.uniform(-0.1, 0.1) for _ in range(768)]
            row = await conn.fetchrow("""
                insert into symptoms (description, appliance_type, embedding)
                values ($1, $2, $3)
                on conflict (description) do nothing returning id
            """, desc, app_type, str(vec))
            
            s_id = row["id"] if row else (await conn.fetchrow("select id from symptoms where description=$1", desc))["id"]
            symptom_id_cache[desc] = s_id
            return s_id

        fridge_symptoms = [
            "Refrigerator is too warm", "Ice maker not making ice",
            "Ice maker won't dispense ice", "Refrigerator is leaking water",
            "Refrigerator is running constantly", "Refrigerator making strange noises",
            "Door bin is cracked or broken", "Refrigerator light bulb burned out"
        ]
        dishwasher_symptoms = [
            "Dishwasher is not draining", "Water remains in the bottom of dishwasher",
            "Dishwasher door latch won't close", "Dishwasher is leaking water",
            "Dishwasher rack won't slide smoothly", "Dishwasher is not cleaning dishes",
            "Dishwasher making loud motor noise"
        ]

        print("Seeding parts...")
        parts_to_seed = _build_hardcoded_parts(fridge_symptoms, dishwasher_symptoms)

        for p in parts_to_seed:
            row = await conn.fetchrow("""
                insert into parts (ps_number, mpn, name, description, price_cents,
                                   in_stock, image_url, rating, review_count, appliance_type, video_url)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                on conflict (ps_number) do update set 
                    price_cents=excluded.price_cents, in_stock=excluded.in_stock,
                    rating=excluded.rating, review_count=excluded.review_count,
                    description=excluded.description, image_url=excluded.image_url
                returning id
            """, p.ps_number, p.mpn, p.name, p.description, p.price_cents,
                p.in_stock, p.image_url, p.rating, p.review_count,
                p.appliance_type, p.video_url)
            part_id = row["id"]

            for alt in p.alt_numbers:
                await conn.execute("""
                    insert into part_cross_refs (part_id, alt_number) values ($1, $2)
                    on conflict do nothing
                """, part_id, alt)

            for s_desc in p.symptoms:
                symptom_id = await get_or_create_symptom(s_desc, p.appliance_type)
                await conn.execute("""
                    insert into part_symptoms (part_id, symptom_id) values ($1, $2)
                    on conflict do nothing
                """, part_id, symptom_id)

            for step in p.install_steps:
                await conn.execute("""
                    insert into installation_steps (part_id, step_no, text, difficulty, est_minutes, video_url)
                    values ($1, $2, $3, $4, $5, $6)
                """, part_id, step["step_no"], step["text"], step.get("difficulty"), step.get("est_minutes"), step.get("video_url"))
            
            print(f"  Part {p.ps_number} loaded successfully.")

        # Compatibility matrix
        print("Linking compatibility matrix...")
        m_rows = await conn.fetch("select id, model_number, appliance_type from appliance_models;")
        model_ids = {r["model_number"]: (r["id"], r["appliance_type"]) for r in m_rows}
        for m_num, (m_id, app_type) in model_ids.items():
            await conn.execute("""
                insert into part_compatibility (part_id, model_id)
                select id, $2 from parts where appliance_type = $1
                on conflict do nothing
            """, app_type, m_id)

        # Repair guides
        print("Seeding repair guides...")
        guides = [
            {
                "appliance_type": "refrigerator", "brand": "Whirlpool",
                "title": "How to Fix a Whirlpool Refrigerator Ice Maker Not Making Ice",
                "body": "If your Whirlpool refrigerator's ice maker stops producing ice, it's typically caused by one of a few common issues. First, ensure the bail wire arm is in the down position. Second, verify that the freezer temperature is below 15°F (-9°C). If it is too warm, ice will not form correctly. Third, inspect the fill tube at the back of the ice maker for ice blockages; if it is blocked, clear it with a hairdryer on low heat. If the fill tube is clear and the temperature is correct, check the water inlet valve. Finally, if none of these fix the issue, you likely need to replace the entire ice maker assembly unit.",
                "source_url": "https://www.partselect.com/repair/refrigerator/ice-maker-not-working/",
                "likely_part_ps": ["PS11738118"]
            },
            {
                "appliance_type": "dishwasher", "brand": "Whirlpool",
                "title": "How to Resolve Dishwasher Not Draining Water",
                "body": "When a dishwasher doesn't drain, you're left with standing water at the bottom of the tub. The common causes include a clogged sink drain line, a dirty air gap, a blocked check valve, or a failed drain pump. Start by inspecting the drain hose connecting the dishwasher to the garbage disposal. If that is clear, remove the filter inside the tub and check for food particles. If the filter is clean, listen for the drain pump when a drain cycle starts. If it hums but does not pump, or makes no sound at all, the drain pump motor is likely burnt out and should be replaced.",
                "source_url": "https://www.partselect.com/repair/dishwasher/not-draining/",
                "likely_part_ps": ["PS11745423"]
            }
        ]

        for g in guides:
            part_ids = []
            for ps in g["likely_part_ps"]:
                row = await conn.fetchrow("select id from parts where ps_number=$1", ps)
                if row:
                    part_ids.append(row["id"])
            
            print(f"  [API] Embedding guide: '{g['title']}'")
            try:
                vec = await embed(g["body"])
            except Exception as e:
                print(f"  [Warning] API call failed: {e}. Using mock embedding.")
                vec = [random.uniform(-0.1, 0.1) for _ in range(768)]
            
            await conn.execute("""
                insert into repair_guides (appliance_type, brand, title, body, source_url, likely_part_ids, embedding)
                values ($1, $2, $3, $4, $5, $6, $7)
            """, g["appliance_type"], g["brand"], g["title"], g["body"], g["source_url"], part_ids, str(vec))
            print(f"  Repair guide '{g['title']}' loaded.")

        # Test order
        await conn.execute("""
            insert into orders (id, session_id, status, tracking_url)
            values ('ORD-10293', 'test-session', 'Shipped', 'https://www.partselect.com/tracking/ORD-10293')
            on conflict do nothing
        """)
        print("  Order ORD-10293 seeded.")
        print("Seeding complete!")

    await close_pool()


# ─── Helpers ─────────────────────────────────────────────────────────

async def _truncate_all(conn):
    """Truncate all tables in dependency order."""
    print("Cleaning old data...")
    tables = [
        "part_compatibility", "part_cross_refs", "part_symptoms",
        "symptoms", "installation_steps", "repair_guides",
        "related_parts", "cart_items", "carts", "orders",
        "parts", "appliance_models", "chat_sessions",
    ]
    for t in tables:
        await conn.execute(f"truncate table {t} cascade;")


def _build_hardcoded_parts(fridge_symptoms, dishwasher_symptoms):
    """Build the list of 60 hardcoded parts for dev/testing."""
    parts = []
    
    # 4 primary parts
    parts.append(ScrapedPart(
        ps_number="PS11752778", mpn="W10318947", name="Refrigerator Door Shelf Bin",
        description="This door shelf bin attaches to the inside of the refrigerator door to hold bottles and jars. It is clear plastic.",
        price_cents=4740, in_stock=True,
        image_url="https://www.partselect.com/images/parts/W10318947.jpg",
        rating=4.8, review_count=24, appliance_type="refrigerator",
        video_url="https://www.youtube.com/watch?v=bin-install",
        symptoms=["Door bin is cracked or broken"],
        alt_numbers=["W10318947", "2319657"],
        install_steps=[
            {"step_no": 1, "text": "Open the refrigerator door fully.", "difficulty": "Easy", "est_minutes": 1},
            {"step_no": 2, "text": "Lift the broken shelf bin straight up to release it from the side support tabs.", "difficulty": "Easy", "est_minutes": 1},
            {"step_no": 3, "text": "Align the new door bin with the tabs on the refrigerator door and push down until it clicks securely into place.", "difficulty": "Easy", "est_minutes": 2}
        ]
    ))
    parts.append(ScrapedPart(
        ps_number="PS11738118", mpn="W10882923", name="Ice Maker Assembly",
        description="This ice maker assembly contains the complete ice maker mould and motor driver module.",
        price_cents=9995, in_stock=True,
        image_url="https://www.partselect.com/images/parts/W10882923.jpg",
        rating=4.5, review_count=110, appliance_type="refrigerator",
        video_url="https://www.youtube.com/watch?v=icemaker-install",
        symptoms=["Ice maker not making ice", "Ice maker won't dispense ice"],
        alt_numbers=["W10882923", "W10377151"],
        install_steps=[
            {"step_no": 1, "text": "Disconnect the power supply and turn off the water line.", "difficulty": "Medium", "est_minutes": 5},
            {"step_no": 2, "text": "Loosen the mounting screws holding the old ice maker assembly.", "difficulty": "Medium", "est_minutes": 10},
            {"step_no": 3, "text": "Unplug the wiring harness, slide the old assembly off, and install the new ice maker.", "difficulty": "Medium", "est_minutes": 15}
        ]
    ))
    parts.append(ScrapedPart(
        ps_number="PS11752779", mpn="W10712395", name="Dishwasher Rack Adjuster",
        description="This upper rack adjuster lets you change the height of your dishwasher rack.",
        price_cents=3450, in_stock=True,
        image_url="https://www.partselect.com/images/parts/W10712395.jpg",
        rating=4.7, review_count=85, appliance_type="dishwasher",
        video_url="https://www.youtube.com/watch?v=rack-adjuster",
        symptoms=["Dishwasher rack won't slide smoothly"],
        alt_numbers=["W10712395"],
        install_steps=[
            {"step_no": 1, "text": "Open dishwasher door and pull out the upper rack.", "difficulty": "Easy", "est_minutes": 2},
            {"step_no": 2, "text": "Pop out the old rack adjuster clips.", "difficulty": "Easy", "est_minutes": 3},
            {"step_no": 3, "text": "Mount the new adjuster assembly onto the rack wires.", "difficulty": "Easy", "est_minutes": 5}
        ]
    ))
    parts.append(ScrapedPart(
        ps_number="PS11745423", mpn="WPW10348269", name="Dishwasher Drain Pump",
        description="The drain pump removes water from the dishwasher tub during the drain phase.",
        price_cents=5495, in_stock=True,
        image_url="https://www.partselect.com/images/parts/WPW10348269.jpg",
        rating=4.6, review_count=50, appliance_type="dishwasher",
        video_url="https://www.youtube.com/watch?v=drain-pump",
        symptoms=["Dishwasher is not draining", "Water remains in the bottom of dishwasher"],
        alt_numbers=["WPW10348269"],
        install_steps=[
            {"step_no": 1, "text": "Shut off the power breaker and water supply.", "difficulty": "Hard", "est_minutes": 10},
            {"step_no": 2, "text": "Remove the lower access panel and disconnect the drain hose.", "difficulty": "Hard", "est_minutes": 15},
            {"step_no": 3, "text": "Twist the drain pump counter-clockwise and install the new pump.", "difficulty": "Hard", "est_minutes": 15}
        ]
    ))

    # 28 more refrigerator parts
    fridge_pool = [
        ("Water Filter", "WF-103", 3299, "Filters chlorine, rust, and odors."),
        ("Door Gasket / Seal", "DG-441", 5850, "Creates an airtight seal on the door."),
        ("Defrost Thermostat", "DT-782", 2145, "Protects the evaporator from overheating."),
        ("Evaporator Fan Motor", "EFM-902", 4560, "Circulates cold air through compartments."),
        ("Defrost Heater", "DH-302", 3780, "Melts frost from the evaporator coils."),
        ("Defrost Timer", "DTR-112", 2895, "Controls the intervals between defrost cycles."),
        ("Temperature Sensor", "TS-551", 1850, "Monitors the temperature within compartments."),
        ("Light Bulb (40W)", "LB-040", 699, "Standard appliance bulb for interior illumination."),
        ("Door Switch", "DS-901", 1250, "Signals the control board when the door is opened."),
        ("Ice Bucket / Auger", "IBA-221", 7250, "Holds ice cubes and rotates the auger to dispense."),
        ("Ice Dispenser Solenoid", "IDS-432", 3400, "Actuates the dispenser flap to release ice."),
        ("Compressor Start Relay", "SR-711", 2495, "Helps start the compressor motor."),
        ("Crisper Drawer / Bin", "CD-091", 4995, "Stores fruits and vegetables at optimal humidity."),
        ("Glass Shelf Assembly", "GS-382", 6450, "Adjustable glass shelf for food storage."),
        ("Air Filter", "AF-900", 1599, "Neutralizes refrigerator odors."),
        ("Control Board", "CB-778", 14500, "Main control board managing cooling cycles."),
        ("Door Handle (Black)", "DH-881", 3995, "Replacement door handle assembly."),
        ("Door Hinge", "DHG-211", 2950, "Supports the door for smooth swing."),
        ("Water Line Tubing", "WT-300", 1299, "Supplies water to filter and ice maker."),
        ("Defrost Drain Tube", "DDT-044", 1450, "Channels melted frost water to the drain pan."),
        ("Condenser Fan Motor", "CFM-911", 4895, "Draws air through the condenser coils."),
        ("Water Filter Housing", "WFH-021", 5500, "Connects the filter to the water lines."),
        ("Wine Rack Insert", "WR-082", 2499, "Wire rack for wine bottles."),
        ("Butter Dish Cover", "BD-002", 995, "Clear plastic flip cover for dairy compartment."),
        ("Shelf Support Clip", "SC-101", 495, "Durable plastic clip holding shelves."),
        ("Ice Dispenser Microswitch", "MS-202", 999, "Detects pressure on dispenser lever."),
        ("Evaporator Coil Assembly", "EC-303", 8500, "Evaporator coils where refrigerant absorbs heat."),
        ("Condenser Coil Cover", "CCC-021", 1850, "Protective back cover for condenser coils."),
    ]
    for i, (name, mpn, price, desc) in enumerate(fridge_pool):
        parts.append(ScrapedPart(
            ps_number=f"PS117528{i:02d}", mpn=mpn, name=f"Refrigerator {name}",
            description=desc, price_cents=price,
            in_stock=random.choice([True, True, False]),
            image_url=f"https://www.partselect.com/images/parts/{mpn}.jpg",
            rating=round(random.uniform(4.0, 5.0), 1),
            review_count=random.randint(5, 150),
            appliance_type="refrigerator", video_url=None,
            symptoms=random.sample(fridge_symptoms, k=2),
            alt_numbers=[mpn],
            install_steps=[
                {"step_no": 1, "text": "Disconnect power from the refrigerator.", "difficulty": "Easy", "est_minutes": 2},
                {"step_no": 2, "text": f"Install the new {name} by reversing disassembly.", "difficulty": "Medium", "est_minutes": 10}
            ]
        ))

    # 28 more dishwasher parts
    dish_pool = [
        ("Water Inlet Valve", "WIV-441", 2995, "Controls the flow of water into the tub."),
        ("Door Gasket Seal", "DGS-882", 4250, "Prevents water from leaking around the door."),
        ("Door Latch & Switch", "DLS-091", 2495, "Keeps the door closed and cuts power if opened."),
        ("Heating Element", "HE-303", 3895, "Heats the water during wash cycles."),
        ("Circulation Pump Motor", "CPM-771", 11500, "Pumps water through the spray arms."),
        ("Drain Hose (6ft)", "DH-006", 1495, "Carries waste water to the drain."),
        ("Fine Filter Mesh", "FFM-101", 1850, "Filters small food debris."),
        ("Coarse Filter / Basket", "CF-202", 1250, "Traps larger food particles."),
        ("Detergent Dispenser Assembly", "DDA-991", 4599, "Holds and releases detergent."),
        ("Rinse Aid Cap", "RAC-001", 799, "Threaded cap for rinse aid reservoir."),
        ("Lower Rack Roller Wheel", "RW-002", 995, "Allows the lower rack to roll out smoothly."),
        ("Tine Row Pivot Clip", "TPC-044", 650, "Clips onto rack wires."),
        ("Door Hinge Cable", "DHC-112", 1295, "Connects the door hinge to the tension spring."),
        ("Door Hinge Spring", "DHS-552", 1550, "Counterbalances the door weight."),
        ("Vent Fan Assembly", "VFA-202", 3650, "Vents hot air during drying."),
        ("Overfill Float Switch", "FS-881", 1699, "Detects if water level is too high."),
        ("Float Dome", "FD-092", 850, "Plastic cylinder that rises with water level."),
        ("Tub Gasket Seal", "TG-301", 3450, "Seals the tub enclosure."),
        ("Spray Arm (Lower)", "SAL-901", 2850, "Rotates at the bottom spraying water up."),
        ("Spray Arm (Upper)", "SAU-902", 2695, "Rotates below the upper rack."),
        ("Control Board / Touchpad", "CB-902", 16800, "Electronic control module."),
        ("Silverware Basket", "SB-082", 2495, "Removable basket for silverware."),
        ("Spray Arm Hub Support", "SAHS-22", 1500, "Holds the spray arm on its axis."),
        ("Heating Element Bracket", "HEB-01", 595, "Clips holding heating element."),
        ("Dishwasher Power Cord", "PC-331", 1499, "Electrical cord supplying power."),
        ("Turbidity Sensor", "TS-042", 2850, "Measures water cleanliness."),
        ("Door Accent Trim", "DT-303", 5500, "Stainless steel trim piece."),
        ("Water Supply Line", "WSL-9", 1999, "Flexible braided steel supply line."),
    ]
    for i, (name, mpn, price, desc) in enumerate(dish_pool):
        parts.append(ScrapedPart(
            ps_number=f"PS117529{i:02d}", mpn=mpn, name=f"Dishwasher {name}",
            description=desc, price_cents=price,
            in_stock=random.choice([True, True, False]),
            image_url=f"https://www.partselect.com/images/parts/{mpn}.jpg",
            rating=round(random.uniform(4.0, 5.0), 1),
            review_count=random.randint(5, 150),
            appliance_type="dishwasher", video_url=None,
            symptoms=random.sample(dishwasher_symptoms, k=2),
            alt_numbers=[mpn],
            install_steps=[
                {"step_no": 1, "text": "Turn off power and water supply.", "difficulty": "Medium", "est_minutes": 5},
                {"step_no": 2, "text": f"Install the new {name} by reversing removal.", "difficulty": "Hard", "est_minutes": 15}
            ]
        ))

    return parts


# ─── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the PartSelect database")
    parser.add_argument("--from-json", action="store_true",
                        help="Load from scraped JSON files instead of hardcoded data")
    parser.add_argument("--reset", action="store_true",
                        help="Truncate all tables before loading (used with --from-json)")
    args = parser.parse_args()

    if args.from_json:
        asyncio.run(seed_from_json(reset=args.reset))
    else:
        asyncio.run(seed_data())
