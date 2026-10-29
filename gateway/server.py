"""Server FastAPI: API OpenAI-compatible + web UI open-masking."""
import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any, List, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from .admin import create_router
from .auth import get_store, require_api_key
from .config import APP_NAME, MODEL_IDS, VERSION, WEB_DIR
from .engine import Completion, GatewayEngine
from .errors import ApiError
from .tools import content_to_text

CREATED = 1760000000

engine = GatewayEngine()


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Any = None
    name: Optional[str] = None
    tool_calls: Optional[List[Any]] = None
    tool_call_id: Optional[str] = None
    reasoning_content: Optional[str] = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "deepseek-chat"
    messages: List[ChatMessage]
    tools: Optional[List[Any]] = None
    tool_choice: Optional[Any] = None
    parallel_tool_calls: Optional[bool] = None
    stream: bool = False
    stream_options: Optional[dict] = None
    thinking: Optional[bool] = None
    ds_thinking: Optional[bool] = None
    enable_thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    reasoning: Optional[Any] = None
    search: Optional[bool] = None
    web_search: Optional[bool] = None
    enable_search: Optional[bool] = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await engine.close()


app = FastAPI(
    title=APP_NAME,
    description="OpenAI-compatible gateway backed by chat.deepseek.com browser automation.",
    version=VERSION,
    lifespan=lifespan,
)

app.include_router(create_router(engine), prefix="/api")


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
    return {
        "status": "ok",
        "service": APP_NAME,
        "version": VERSION,
        "configured_keys": get_store().has_active_keys(),
    }


@app.get("/v1/models")
async def list_models(_key=Depends(require_api_key)):
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": CREATED,
                "owned_by": APP_NAME,
            }
            for model_id in MODEL_IDS
        ],
    }


@app.post("/v1/chat/completions")
async def create_chat_completion(request: ChatRequest, _key=Depends(require_api_key)):
    if not request.messages:
        raise ApiError(400, "messages tidak boleh kosong")

    if request.stream:
        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        if not _stream_available(request):
            # Tool calling memerlukan hasil lengkap untuk mem-parse tool_call.
            # Jalankan sebelum HTTP streaming dimulai supaya error upstream menjadi
            # respons 503/504 valid, bukan SSE 200 yang tidak berisi choices.
            try:
                result = await engine.complete(request)
            except asyncio.TimeoutError as exc:
                raise ApiError(504, f"DeepSeek timeout: {exc}", code="upstream_timeout") from exc
            except Exception as exc:
                raise ApiError(503, f"Browser automation gagal: {exc}", code="upstream_error") from exc
            return StreamingResponse(
                _stream_result(request, result),
                media_type="text/event-stream",
                headers=headers,
            )
        return StreamingResponse(
            _stream_completion(request),
            media_type="text/event-stream",
            headers=headers,
        )

    try:
        result = await engine.complete(request)
    except asyncio.TimeoutError as exc:
        raise ApiError(504, f"DeepSeek timeout: {exc}", code="upstream_timeout")
    except Exception as exc:
        raise ApiError(503, f"Browser automation gagal: {exc}", code="upstream_error") from exc

    return _completion_response(request, result)


def _usage(request: ChatRequest, result: Completion) -> dict:
    prompt_chars = sum(len(content_to_text(message.content)) for message in request.messages)
    completion_chars = len(result.content or "") + len(result.reasoning or "")
    prompt_tokens = max(1, prompt_chars // 4)
    completion_tokens = max(1, completion_chars // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _completion_response(request: ChatRequest, result: Completion) -> dict:
    message = {"role": "assistant", "content": result.content}
    if result.reasoning:
        message["reasoning_content"] = result.reasoning
    if result.tool_calls:
        message["tool_calls"] = result.tool_calls

    return {
        "id": "chatcmpl-" + secrets.token_hex(12),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": result.finish_reason,
            }
        ],
        "usage": _usage(request, result),
    }


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _chunk(request: ChatRequest, completion_id: str, created: int, delta: dict, finish: Optional[str] = None) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": request.model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _stream_available(request: ChatRequest) -> bool:
    """Streaming live hanya aman bila tidak ada tool yang perlu di-parse."""
    has_tools = bool(request.tools) and request.tool_choice != "none"
    return not has_tools


async def _stream_result(request: ChatRequest, result: Completion):
    """Serialize a completed result as a valid OpenAI-compatible SSE stream."""
    completion_id = "chatcmpl-" + secrets.token_hex(12)
    created = int(time.time())
    include_usage = bool((request.stream_options or {}).get("include_usage"))

    yield _sse(_chunk(request, completion_id, created, {"role": "assistant", "content": ""}))
    if result.reasoning:
        yield _sse(_chunk(request, completion_id, created, {"reasoning_content": result.reasoning}))
    if result.content:
        yield _sse(_chunk(request, completion_id, created, {"content": result.content}))
    if result.tool_calls:
        deltas = [
            {
                "index": index,
                "id": call["id"],
                "type": "function",
                "function": call["function"],
            }
            for index, call in enumerate(result.tool_calls)
        ]
        yield _sse(_chunk(request, completion_id, created, {"tool_calls": deltas}))
    yield _sse(_chunk(request, completion_id, created, {}, result.finish_reason))
    if include_usage:
        usage_chunk = _chunk(request, completion_id, created, {}, None)
        usage_chunk["choices"] = []
        usage_chunk["usage"] = _usage(request, result)
        yield _sse(usage_chunk)
    yield "data: [DONE]\\n\\n"


async def _stream_completion(request: ChatRequest):
    """Live SSE stream for requests that do not ask the gateway to parse tools."""
    completion_id = "chatcmpl-" + secrets.token_hex(12)
    created = int(time.time())
    include_usage = bool((request.stream_options or {}).get("include_usage"))
    yield _sse(_chunk(request, completion_id, created, {"role": "assistant", "content": ""}))

    queue: asyncio.Queue = asyncio.Queue()

    def on_content(chunk: str):
        queue.put_nowait(("content", chunk))

    def on_reasoning(chunk: str):
        queue.put_nowait(("reasoning", chunk))

    async def runner():
        try:
            result = await engine.complete(request, on_content=on_content, on_reasoning=on_reasoning)
            await queue.put(("done", result))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(("error", exc))

    task = asyncio.create_task(runner())
    result: Optional[Completion] = None
    error_message: Optional[str] = None
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "content":
                yield _sse(_chunk(request, completion_id, created, {"content": payload}))
            elif kind == "reasoning":
                yield _sse(_chunk(request, completion_id, created, {"reasoning_content": payload}))
            elif kind == "error":
                error_message = f"Browser automation gagal: {payload}"
                break
            else:
                result = payload
                break
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except BaseException:
            pass

    if result is not None:
        yield _sse(_chunk(request, completion_id, created, {}, result.finish_reason))
        if include_usage:
            usage_chunk = _chunk(request, completion_id, created, {}, None)
            usage_chunk["choices"] = []
            usage_chunk["usage"] = _usage(request, result)
            yield _sse(usage_chunk)
    elif error_message:
        # Setelah header SSE dikirim, status HTTP tak lagi bisa diubah.
        # Tetap kirim chunk yang dapat diparse klien chat-completions.
        yield _sse(_chunk(request, completion_id, created, {"content": f"[open-masking error] {error_message}"}))
        yield _sse(_chunk(request, completion_id, created, {}, "stop"))
    yield "data: [DONE]\\n\\n"

# Web UI (didaftarkan terakhir agar /v1 dan /api tetap menang).
app.mount("/assets", StaticFiles(directory=str(WEB_DIR)), name="assets")

@app.get("/app")
def landing():
    return FileResponse(WEB_DIR / "index.html")

@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(WEB_DIR / "landing.html")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    return FileResponse(WEB_DIR / "favicon.svg")
