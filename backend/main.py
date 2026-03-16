import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import init_db
from routers import admin, dashboard, webhooks
from scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Bridge starting up")
    # Ensure screenshots directory exists before mounting static files
    Path("screenshots").mkdir(exist_ok=True)
    await init_db()
    # Scheduler only runs in production — dev/test environments skip it
    if os.getenv("ENVIRONMENT", "production") == "production":
        start_scheduler()
    yield
    if os.getenv("ENVIRONMENT", "production") == "production":
        stop_scheduler()
    logger.info("Bridge shutting down")


app = FastAPI(
    title="Bridge",
    description="Razorpay-to-Substack subscription sync for The Wire",
    version="1.0.0",
    lifespan=lifespan,
)

_frontend_url = os.getenv("FRONTEND_URL", "")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_url] if _frontend_url else [],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(webhooks.router)
app.include_router(admin.router)
app.include_router(dashboard.router)

# Serve screenshot files for the dashboard thumbnail display
app.mount("/screenshots", StaticFiles(directory="screenshots"), name="screenshots")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": os.getenv("ENVIRONMENT", "production"),
    }
