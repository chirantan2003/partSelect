import asyncio
import asyncpg
import os

pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global pool
    current_loop = asyncio.get_running_loop()
    if pool is None or pool._loop is not current_loop or pool._closed:
        if pool is not None:
            try:
                await pool.close()
            except Exception:
                pass
        pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    return pool

async def get_conn():
    p = await get_pool()
    return await p.acquire()

async def release_conn(conn):
    p = await get_pool()
    await p.release(conn)

async def close_pool():
    global pool
    if pool is not None:
        await pool.close()
        pool = None
