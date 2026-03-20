from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.connections import router as connections_router
from app.api.input_connections import router as input_connections_router
from app.api.jobs import router as jobs_router
from app.api.load_plans import router as load_plans_router
from app.api.load_runs import router as load_runs_router
from app.api.load_steps import router as load_steps_router
from app.api.utility import router as utility_router
from app.api.utility import ws_router
from app.config import settings
from app.database import AsyncSessionLocal, engine
from app.services.auth import seed_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: seed initial admin user if database is empty
    async with AsyncSessionLocal() as session:
        await seed_admin(session)
    yield
    # Shutdown: dispose engine connection pool
    await engine.dispose()


app = FastAPI(
    title="Salesforce Bulk Loader",
    description="Orchestrates large-scale data loads into Salesforce using the Bulk API 2.0.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routers — each owns its own prefix
app.include_router(auth_router)
app.include_router(connections_router)
app.include_router(input_connections_router)
app.include_router(load_plans_router)
app.include_router(load_steps_router)
app.include_router(load_runs_router)
app.include_router(jobs_router)
app.include_router(utility_router)

# WebSocket router — no prefix, path is /ws/runs/{run_id}
app.include_router(ws_router)
