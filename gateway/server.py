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
from .tools import content_to_text, normalize_tool_request

CREATED = 1760000000

engine = GatewayEngine()


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Any = None
    name: Optional[str] = None
    tool_calls: Optional[List[Any]] = None
    tool_call_id: Optional[str] = None
    function_call: Optional[dict] = None
    reasoning_content: Optional[str] = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "deepseek-chat"
    messages: List[ChatMessage]
    tools: Optional[List[Any]] = None
    tool_choice: Optional[Any] = None
    parallel_tool_calls: Optional[bool] = None
    # Deprecated OpenAI function-calling fields, retained for older clients.
    functions: Optional[List[Any]] = None
    function_call: Optional[Any] = None
    n: Optional[int] = 1
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
    if request.n not in (None, 1):
        raise ApiError(400, "Gateway browser hanya menghasilkan satu choice; gunakan n=1.")
    if request.tools and request.functions:
        raise ApiError(400, "Gunakan tools (format modern) atau functions (format lama), jangan keduanya sekaligus.")
    if request.function_call is not None and not request.functions:
        raise ApiError(400, "function_call membutuhkan functions; gunakan tool_choice bersama tools untuk format modern.")

    tools, tool_choice, _, _ = normalize_tool_request(
        tools=request.tools,
        tool_choice=request.tool_choice,
        parallel=request.parallel_tool_calls,
        functions=request.functions,
        function_call=request.function_call,
    )
    if tool_choice == "required" and not tools:
        raise ApiError(400, "tool_choice=required membutuhkan setidaknya satu tool.")
    if isinstance(tool_choice, str) and tool_choice not in ("auto", "none", "required"):
        raise ApiError(400, f"tool_choice tidak didukung: {tool_choice}")
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") != "function" or not isinstance(tool_choice.get("function"), dict):
            raise ApiError(400, "tool_choice object harus memakai type=function dan field function.")
        selected = tool_choice["function"].get("name")
        if selected and selected not in {
            ((item.get("function") or item).get("name"))
            for item in (tools or [])
            if isinstance(item, dict) and isinstance(item.get("function", item), dict)
        }:
            raise ApiError(400, f"Tool yang dipilih tidak terdaftar: {selected}")

    if request.stream:
        return StreamingResponse(
            _stream_completion(request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
    tools, _, _, _ = normalize_tool_request(
        tools=request.tools,
        functions=request.functions,
        tool_choice=request.tool_choice,
        parallel=request.parallel_tool_calls,
        function_call=request.function_call,
    )
    prompt_chars += len(json.dumps(tools or [], ensure_ascii=False))
    tool_chars = sum(
        len((call.get("function") or {}).get("name", ""))
        + len((call.get("function") or {}).get("arguments", ""))
        for call in (result.tool_calls or [])
    )
    completion_chars = len(result.content or "") + len(result.reasoning or "") + tool_chars
    prompt_tokens = max(1, prompt_chars // 4)
    completion_tokens = max(1, completion_chars // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _completion_response(request: ChatRequest, result: Completion) -> dict:
    legacy_functions = not bool(request.tools) and bool(request.functions)
    message = {"role": "assistant", "content": result.content}
    if result.reasoning:
        message["reasoning_content"] = result.reasoning

    finish_reason = result.finish_reason
    if result.tool_calls and legacy_functions:
        first = result.tool_calls[0]["function"]
        message["function_call"] = {
            "name": first["name"],
            "arguments": first["arguments"],
        }
        finish_reason = "function_call"
    elif result.tool_calls:
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
                "finish_reason": finish_reason,
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
    """Live text streaming is safe only when the output need not be parsed for tool calls."""
    tools, tool_choice, _, _ = normalize_tool_request(
        tools=request.tools,
        tool_choice=request.tool_choice,
        parallel=request.parallel_tool_calls,
        functions=request.functions,
        function_call=request.function_call,
    )
    return not bool(tools) or tool_choice == "none"

async def _stream_completion(request: ChatRequest):
    """OpenAI-compatible SSE; buffer tool calls but always end with valid chunks."""
    completion_id = "chatcmpl-" + secrets.token_hex(12)
    created = int(time.time())
    include_usage = bool((request.stream_options or {}).get("include_usage"))
    yield _sse(_chunk(request, completion_id, created, {"role": "assistant", "content": ""}))

    if not _stream_available(request):
        # Tool calling needs the complete text so we can parse the gateway tool-call format.
        try:
            result = await engine.complete(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = f"DeepSeek timeout: {exc}" if isinstance(exc, asyncio.TimeoutError) else f"Browser automation gagal: {exc}"
            # Once SSE starts, return a parseable assistant chunk rather than a raw
            # JSON error object that some OpenAI-compatible clients treat as no response.
            yield _sse(_chunk(request, completion_id, created, {"content": f"[open-masking error] {message}"}))
            yield _sse(_chunk(request, completion_id, created, {}, "stop"))
            yield "data: [DONE]\n\n"
            return

        if result.reasoning:
            yield _sse(_chunk(request, completion_id, created, {"reasoning_content": result.reasoning}))
        if result.content:
            yield _sse(_chunk(request, completion_id, created, {"content": result.content}))
        legacy_functions = not bool(request.tools) and bool(request.functions)
        finish_reason = result.finish_reason
        if result.tool_calls and legacy_functions:
            first = result.tool_calls[0]["function"]
            yield _sse(_chunk(
                request, completion_id, created,
                {"function_call": {"name": first["name"], "arguments": first["arguments"]}},
            ))
            finish_reason = "function_call"
        elif result.tool_calls:
            # Send proper OpenAI-style tool-call deltas: index/id/type/name first,
            # then the JSON arguments. Clients can incrementally assemble the call.
            for index, call in enumerate(result.tool_calls):
                function = call["function"]
                yield _sse(_chunk(request, completion_id, created, {
                    "tool_calls": [{
                        "index": index,
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": function["name"], "arguments": ""},
                    }]
                }))
                arguments = function["arguments"]
                if arguments:
                    yield _sse(_chunk(request, completion_id, created, {
                        "tool_calls": [{
                            "index": index,
                            "function": {"arguments": arguments},
                        }]
                    }))
        yield _sse(_chunk(request, completion_id, created, {}, finish_reason))
        if include_usage:
            usage_chunk = _chunk(request, completion_id, created, {}, None)
            usage_chunk["choices"] = []
            usage_chunk["usage"] = _usage(request, result)
            yield _sse(usage_chunk)
        yield "data: [DONE]\n\n"
        return

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
        yield _sse(_chunk(request, completion_id, created, {"content": f"[open-masking error] {error_message}"}))
        yield _sse(_chunk(request, completion_id, created, {}, "stop"))
    yield "data: [DONE]\n\n"

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
