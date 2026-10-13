"""
Minimal FastAPI service demonstrating production patterns:
- /health   -> liveness probe: "is the process alive at all?"
- /ready    -> readiness probe: "can this pod actually serve traffic?"
- /items    -> a real endpoint backed by Postgres

Why separate /health and /ready?
Kubernetes uses these differently:
  - livenessProbe failing  -> K8s KILLS and RESTARTS the pod
  - readinessProbe failing -> K8s just STOPS SENDING TRAFFIC to the pod (no restart)

If you conflate them (one endpoint for both), a slow DB will cause K8s to
restart your app in a crash loop instead of just waiting for the DB to
recover -- a very common production incident caused by bad probe design.
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# --- Structured logging setup -------------------------------------------
# In production you almost never want plain print() or default text logs.
# JSON logs are what Loki/CloudWatch/ELK expect so they can be parsed,
# filtered, and alerted on (you've already dealt with this via LOKI_URL).
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    stream=sys.stdout,  # containers should log to stdout/stderr, never to files
)
log = logging.getLogger("app")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://appuser:apppass@localhost:5432/appdb")

# How long to wait between connection attempts. Starts small (fast recovery
# when the DB is only a few seconds late) and doubles up to a ceiling (so we
# don't hammer a database that is genuinely down).
INITIAL_RETRY_DELAY = 1
MAX_RETRY_DELAY = 30

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
)
"""

# --- DB connection pool ---------------------------------------------------
# A pool (not a single connection) is created once and reused.
# Opening a new DB connection per-request is a classic scaling mistake.
# It stays None until the database has actually been reached, which is
# exactly what /ready checks.
db_pool: asyncpg.Pool | None = None


async def connect_with_retry():
    """
    Keep trying to reach the database until it works, then store the pool.

    This replaces the old "try once at startup and give up" behaviour. In
    Kubernetes the app and the database often start at the same moment, so
    the database being a few seconds late is normal, not an error.
    """
    global db_pool
    delay = INITIAL_RETRY_DELAY
    while True:
        pool = None
        try:
            pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=1,
                max_size=5,
                timeout=5,  # fail fast instead of hanging forever
            )
            async with pool.acquire() as conn:
                await conn.execute(CREATE_TABLE_SQL)
            db_pool = pool
            log.info("Database ready.")
            return
        except asyncio.CancelledError:
            # The app is shutting down; stop retrying and don't swallow this.
            if pool:
                await pool.close()
            raise
        except Exception as e:
            if pool:
                await pool.close()  # don't leak a half-set-up pool
            log.error(f"DB connection failed, retrying in {delay}s: {e}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RETRY_DELAY)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start connecting in the background and let the app come up immediately,
    # so /health passes (process is alive) while /ready correctly reports
    # "not ready" until the database answers.
    log.info("Starting up, connecting to database in the background...")
    connect_task = asyncio.create_task(connect_with_retry())

    yield  # app runs here

    log.info("Shutting down...")
    connect_task.cancel()
    try:
        await connect_task
    except asyncio.CancelledError:
        pass
    if db_pool:
        await db_pool.close()


app = FastAPI(title="gitops-demo-api", lifespan=lifespan)


@app.get("/health")
async def health():
    """Liveness: is the Python process itself responsive? No dependency checks."""
    return {"status": "alive"}


@app.get("/ready")
async def ready():
    """Readiness: can we actually serve real traffic right now?"""
    if db_pool is None:
        return JSONResponse(status_code=503, content={"status": "not ready", "reason": "db pool not initialized"})
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("SELECT 1")
        return {"status": "ready"}
    except Exception as e:
        log.warning(f"Readiness check failed: {e}")
        return JSONResponse(status_code=503, content={"status": "not ready", "reason": str(e)})


@app.get("/items")
async def list_items():
    if db_pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, created_at FROM items ORDER BY id DESC LIMIT 50")
        return [dict(r) for r in rows]


@app.post("/items")
async def create_item(name: str):
    if db_pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO items (name) VALUES ($1) RETURNING id, name, created_at", name
        )
        log.info(f"Created item id={row['id']} name={row['name']}")
        return dict(row)
