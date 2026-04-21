import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.observability.error_monitoring import configure_error_monitoring
from app.observability.logging_config import configure_logging
from app.observability.metrics_middleware import MetricsMiddleware
from app.observability.middleware import RequestIDMiddleware
from app.observability.tracing import configure_tracing, instrument_fastapi_app

configure_logging(settings)
configure_tracing(settings)
configure_error_monitoring(settings)

logger = logging.getLogger(__name__)

from app.observability.events import EmailEvent, OutcomeCode
from app.api.admin_email import router as admin_email_router
from app.api.admin_users import router as admin_users_router
from app.api.settings import router as settings_router
from app.api.auth import router as auth_router
from app.api.auth_reset import router as auth_reset_router
from app.api.me import router as me_router
from app.api.profile import router as profile_router
from app.api.connections import router as connections_router
from app.api.input_connections import router as input_connections_router
from app.api.jobs import router as jobs_router
from app.api.load_plans import router as load_plans_router
from app.api.load_runs import router as load_runs_router
from app.api.load_steps import router as load_steps_router
from app.api.notification_subscriptions import router as notification_subscriptions_router
from app.api.utility import router as utility_router
from app.api.utility import ws_router
from app.auth.permissions import ALL_PERMISSION_KEYS
from app.database import AsyncSessionLocal, engine
from app.services.auth import seed_admin
from app.services.email import delivery_log as email_delivery_log
from app.services.email import init_email_service
from app.services.email.service import get_email_service, init_email_service_async
from app.services.notifications import init_notification_dispatcher
from app.services.settings.service import init_settings_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: seed DB-backed settings from env vars / registry defaults
    _svc = init_settings_service(AsyncSessionLocal)
    await _svc.seed_from_env()

    # Startup: validate permission keys in profile_permissions against the known
    # vocabulary. Catches typos in seed data or hand-edits before they cause
    # silent auth failures.
    from sqlalchemy import text as _sql_text
    async with AsyncSessionLocal() as _perm_session:
        _rows = await _perm_session.execute(
            _sql_text("SELECT DISTINCT permission_key FROM profile_permissions")
        )
        _db_keys = {row[0] for row in _rows}
    _unknown = _db_keys - ALL_PERMISSION_KEYS
    if _unknown:
        raise RuntimeError(
            f"Unknown permission key(s) in profile_permissions table: {sorted(_unknown)}. "
            "Update app.auth.permissions.ALL_PERMISSION_KEYS or fix the seed data."
        )

    # Startup: seed initial admin user if database is empty
    async with AsyncSessionLocal() as session:
        await seed_admin(session)

    # Startup: initialise email service singleton (reads email_backend from DB)
    await init_email_service_async(AsyncSessionLocal)

    # Startup: initialise notification dispatcher singleton (SFBL-181)
    init_notification_dispatcher(await get_email_service(), AsyncSessionLocal)

    # Startup: boot-sweep — reap any stale pending email_delivery rows left
    # over from a crashed or OOM-killed process.
    _stale_minutes = await _svc.get("email_pending_stale_minutes")
    async with AsyncSessionLocal() as session:
        reaped = await email_delivery_log.boot_sweep(
            session, _stale_minutes
        )
    logger.info(
        "Email boot-sweep completed",
        extra={
            "event_name": EmailEvent.BOOT_SWEEP_COMPLETED,
            "outcome_code": OutcomeCode.OK if reaped == 0 else OutcomeCode.DEGRADED,
            "reaped_count": reaped,
        },
    )

    logger.info(
        "Distribution profile: %s | auth=%s | transport=%s | storage=%s",
        settings.app_distribution,
        settings.auth_mode,
        settings.transport_mode,
        settings.input_storage_mode,
    )
    if settings.transport_mode == "https":
        logger.info(
            "Transport mode: https — HTTPS must be enforced at the reverse proxy or load balancer. "
            "The backend listens on plain HTTP internally."
        )
    elif settings.transport_mode == "local":
        logger.info(
            "Transport mode: local — backend should be accessible on loopback only. "
            "Ensure the process is not exposed beyond localhost."
        )
    yield
    # Shutdown: dispose engine connection pool
    await engine.dispose()


app = FastAPI(
    title="Salesforce Bulk Loader",
    description="Orchestrates large-scale data loads into Salesforce using the Bulk API 2.0.",
    version="0.1.0",
    lifespan=lifespan,
)
instrument_fastapi_app(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestIDMiddleware, settings=settings)

# REST routers — each owns its own prefix
# Admin routers are only available on hosted profiles (not desktop)
if settings.auth_mode != "none":
    app.include_router(admin_email_router)
    app.include_router(admin_users_router)
    app.include_router(settings_router)
app.include_router(auth_router)
app.include_router(auth_reset_router)
app.include_router(me_router)
app.include_router(profile_router)
app.include_router(connections_router)
app.include_router(input_connections_router)
app.include_router(load_plans_router)
app.include_router(load_steps_router)
app.include_router(load_runs_router)
app.include_router(jobs_router)
app.include_router(notification_subscriptions_router)
app.include_router(utility_router)

# WebSocket router — no prefix, path is /ws/runs/{run_id}
app.include_router(ws_router)
