"""Server FastAPI: web UI Open Gateway + API admin."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .admin import create_router
from .config import APP_NAME, VERSION, WEB_DIR
from .discord import get_hub
from .errors import ApiError
from .store import get_store

hub = get_hub()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_store().ensure()
    await hub.autostart()
    yield
    await hub.shutdown()


app = FastAPI(
    title=APP_NAME,
    description="Hubungkan model AI OpenAI-compatible ke gateway Discord dan WhatsApp.",
    version=VERSION,
    lifespan=lifespan,
)

app.include_router(create_router(), prefix="/api")


@app.exception_handler(ApiError)
async def _api_error_handler(_request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(_request: Request, exc: RequestValidationError):
    error = ApiError(400, f"Request tidak valid: {exc.errors()}", code="invalid_request")
    return JSONResponse(status_code=400, content=error.to_dict())


@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(_request: Request, exc: StarletteHTTPException):
    error = ApiError(exc.status_code, str(exc.detail), code="http_error")
    return JSONResponse(status_code=exc.status_code, content=error.to_dict())


@app.get("/health")
async def health_check():
    store = get_store()
    providers = store.list_providers()
    configured = any(p.get("base_url") and p.get("api_key") for p in providers)
    statuses = hub.status()
    state = "stopped"
    if isinstance(statuses, list) and statuses:
        state = statuses[0].get("state") or "stopped"
    elif isinstance(statuses, dict):
        state = statuses.get("state") or "stopped"
    return {
        "status": "ok",
        "service": APP_NAME,
        "version": VERSION,
        "provider_configured": configured,
        "provider_count": len(providers),
        "gateway_count": len(store.list_gateways()),
        "bot": state,
    }


# Web UI (didaftarkan terakhir agar /api tetap menang).
app.mount("/assets", StaticFiles(directory=str(WEB_DIR)), name="assets")


@app.get("/app")
def app_page():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(WEB_DIR / "landing.html")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    return FileResponse(WEB_DIR / "favicon.svg")
