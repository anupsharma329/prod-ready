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

# --- DB connection pool ---------------------------------------------------
# A pool (not a single connection) is created once at startup and reused.
# Opening a new DB connection per-request is a classic scaling mistake.
db_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    log.info("Starting up, connecting to database...")
    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5,
            timeout=5,  # fail fast instead of hanging forever
        )
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        log.info("Database ready.")
    except Exception as e:
        # We deliberately do NOT crash here. The app should still start so
        # that /health passes (process is alive) even if the DB is briefly
        # unreachable. /ready will correctly report "not ready" instead.
        log.error(f"DB connection failed at startup: {e}")
        db_pool = None

    yield  # app runs here

    log.info("Shutting down, closing DB pool...")
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