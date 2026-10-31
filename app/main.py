"""FastAPI app: lifespan owns the Mongo client; routers are mounted per channel and for the dashboard."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import config, db, events, grants, registry
from app.api import dashboard, debug
from app.channels import email_agentmail, voice, web, whatsapp_twilio


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await grants.ensure_defaults()
    yield
    await db.close()


app = FastAPI(title=config.PRODUCT_NAME, lifespan=lifespan)
app.include_router(web.router)
app.include_router(voice.router)
app.include_router(debug.router)
app.include_router(dashboard.router)
app.include_router(whatsapp_twilio.router)
app.include_router(email_agentmail.router)
if registry.RUNTIME and "paytm" in registry.RUNTIME.pack.channels:  # simulated Paytm chat (fake data), only for packs that enable it
    from app.channels import paytm
    app.include_router(paytm.router)
if registry.RUNTIME and "issue_coupon" in registry.RUNTIME.plugins:  # the simulated "Paytm insights" feed
    from app.api import insights
    app.include_router(insights.router)
app.mount("/static", StaticFiles(directory=f"{config.ROOT}/static"), name="static")


@app.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse("/static/dashboard.html")


@app.get("/health")
async def health() -> dict:
    await db.db.command("ping")
    return {"ok": True, "product": config.PRODUCT_NAME}


@app.get("/api/events")
async def sse(session: str | None = None) -> StreamingResponse:
    return StreamingResponse(events.stream(session), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
