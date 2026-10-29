"""Endpoint admin untuk web UI open-masking (hanya localhost)."""
import asyncio
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .auth import get_store
from .config import (
    APP_NAME,
    DATA_DIR,
    HOST,
    MAX_PROMPT_CHARS,
    MODEL_IDS,
    PORT,
    PROFILE_DIR,
    REPLY_TIMEOUT,
    VERSION,
)
from .engine import GatewayEngine
from .errors import ApiError

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


async def require_local(request: Request):
    host = (request.client.host if request.client else "") or ""
    if host not in LOCAL_HOSTS:
        raise ApiError(
            403,
            "Halaman admin hanya bisa diakses dari localhost.",
            error_type="permission_error",
            code="forbidden",
        )


class KeyCreateRequest(BaseModel):
    name: Optional[str] = None


class PlaygroundMessage(BaseModel):
    role: str
    content: str = ""


class PlaygroundRequest(BaseModel):
    model: str = "deepseek-chat"
    messages: List[PlaygroundMessage]
    thinking: Optional[bool] = None
    search: Optional[bool] = False


class ConfirmRequest(BaseModel):
    confirm: bool = False


def _base_url() -> str:
    host = HOST
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{PORT}/v1"


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def create_router(engine: GatewayEngine) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_local)])

    @router.get("/status")
    async def status():
        keys = get_store().list_keys()
        return {
            "app": APP_NAME,
            "version": VERSION,
            "base_url": _base_url(),
            "models": list(MODEL_IDS),
            "browser": await engine.browser_status(),
            "keys": {
                "total": len(keys),
                "active": sum(1 for key in keys if not key.get("revoked")),
            },
            "limits": {
                "max_prompt_chars": MAX_PROMPT_CHARS,
                "reply_timeout": REPLY_TIMEOUT,
            },
            "paths": {
                "data": str(DATA_DIR),
                "profile": str(PROFILE_DIR),
            },
        }

    @router.get("/keys")
    async def list_keys():
        return {"keys": get_store().list_keys()}

    @router.post("/keys")
    async def create_key(body: KeyCreateRequest):
        record, raw = get_store().create(body.name or "opencode")
        return {"key": raw, "record": record}

    @router.post("/keys/{key_id}/revoke")
    async def revoke_key(key_id: str):
        if not get_store().revoke(key_id):
            raise ApiError(404, "API key tidak ditemukan.", code="key_not_found")
        return {"ok": True}

    @router.get("/browser")
    async def browser_status():
        return await engine.browser_status()

    @router.post("/browser/login")
    async def browser_login():
        started = engine.start_login()
        return {"started": started, "browser": await engine.browser_status()}

    @router.post("/browser/restart")
    async def browser_restart():
        try:
            await engine.restart_browser()
        except Exception as exc:
            raise ApiError(503, f"Browser gagal dijalankan: {exc}", code="browser_error") from exc
        return {"browser": await engine.browser_status()}

    @router.post("/browser/close")
    async def browser_close():
        await engine.close_browser()
        return {"browser": await engine.browser_status()}

    @router.post("/browser/reset")
    async def browser_reset(body: ConfirmRequest):
        if not body.confirm:
            raise ApiError(
                400,
                "Kirim {\"confirm\": true} untuk menghapus profil Chrome dan cookies.",
                code="confirmation_required",
            )
        await engine.reset_profile()
        return {"browser": await engine.browser_status()}

    @router.post("/playground/reset")
    async def playground_reset():
        await engine.reset_conversation()
        return {"ok": True}

    @router.post("/playground/chat")
    async def playground_chat(body: PlaygroundRequest):
        if not body.messages:
            raise ApiError(400, "Pesan tidak boleh kosong.")
        if not any(message.role in ("user", "tool") for message in body.messages):
            raise ApiError(400, "Tidak ada pesan pengguna.")

        payload = {
            "model": body.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in body.messages
            ],
            "thinking": body.thinking,
            "search": body.search,
            "tools": None,
            "tool_choice": None,
            "parallel_tool_calls": None,
        }
        return StreamingResponse(
            _playground_stream(engine, payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/connect")
    async def connect_info():
        keys = [key for key in get_store().list_keys() if not key.get("revoked")]
        return {
            "app": APP_NAME,
            "base_url": _base_url(),
            "models": list(MODEL_IDS),
            "active_keys": len(keys),
            "key_prefixes": [key.get("prefix") for key in keys],
            "paths": {
                "data": str(DATA_DIR),
                "profile": str(PROFILE_DIR),
            },
        }

    return router


async def _playground_stream(engine: GatewayEngine, payload: dict):
    queue: asyncio.Queue = asyncio.Queue()

    def on_content(text: str):
        queue.put_nowait(("content", text))

    def on_reasoning(text: str):
        queue.put_nowait(("reasoning", text))

    async def runner():
        try:
            result = await engine.complete(
                payload, on_content=on_content, on_reasoning=on_reasoning
            )
            await queue.put(("done", result))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(("error", str(exc)))

    task = asyncio.create_task(runner())
    try:
        while True:
            kind, data = await queue.get()
            if kind == "error":
                yield _sse({"type": "error", "message": data})
                break
            if kind == "done":
                yield _sse({"type": "done", "finish_reason": data.finish_reason})
                break
            yield _sse({"type": kind, "text": data})
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except BaseException:
            pass
    yield "data: [DONE]\n\n"
