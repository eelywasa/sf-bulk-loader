from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: nothing to do — Alembic manages schema
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
    allow_origins=["http://localhost:3000"] if settings.app_env == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["utility"])
async def health_check() -> dict:
    return {"status": "ok", "env": settings.app_env}
