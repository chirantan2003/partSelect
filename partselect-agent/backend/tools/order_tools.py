# backend/tools/order_tools.py
from langchain_core.tools import tool
from backend.db import get_pool

@tool
async def add_to_cart(ps_number: str, qty: int = 1, session_id: str = "default") -> dict:
    """Add a part to the shopping cart for a given session. Returns updated cart summary."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1) Get part details
        part = await conn.fetchrow("select id, name, price_cents from parts where ps_number=$1", ps_number)
        if not part:
            return {"error": "part_not_found", "ps_number": ps_number}
        
        # 2) Get or create cart for session
        cart = await conn.fetchrow("""
            insert into carts (session_id) values ($1)
            on conflict (session_id) do update set session_id=excluded.session_id
            returning id
        """, session_id)
        cart_id = cart["id"]
        
        # 3) Upsert item
        await conn.execute("""
            insert into cart_items (cart_id, part_id, qty)
            values ($1, $2, $3)
            on conflict (cart_id, part_id) do update set qty = cart_items.qty + excluded.qty
        """, cart_id, part["id"], qty)
        
        # 4) Fetch full cart summary
        rows = await conn.fetch("""
            select p.ps_number, p.name, p.price_cents, ci.qty
            from cart_items ci
            join parts p on p.id = ci.part_id
            where ci.cart_id = $1
        """, cart_id)
        
        items = [dict(r) for r in rows]
        total_cents = sum(item["price_cents"] * item["qty"] for item in items)
        
        return {
            "status": "ok",
            "added": {"ps_number": ps_number, "qty": qty, "name": part["name"]},
            "cart": {
                "session_id": session_id,
                "items": items,
                "total_price_cents": total_cents
            }
        }

@tool
async def get_order_status(order_id: str) -> dict:
    """Look up a PartSelect order by order ID (e.g. ORD-10293)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("select * from orders where id=$1", order_id)
    if not row:
        return {"error": "order_not_found", "order_id": order_id,
                "hint": "Check the order number on your confirmation email. It should look like ORD-XXXXX."}
    return dict(row)
