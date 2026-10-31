"""Klien provider AI OpenAI-compatible: chat, model, web, gambar, dan audio."""
import asyncio
import base64
import json
from typing import Any, Dict, Iterable, List, Optional

import httpx

from gateway.config import MODELS_TIMEOUT, PROVIDER_TIMEOUT

MODEL_KINDS = ("image", "tts", "stt", "web", "embedding")


class ProviderError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _join(base_url: str, path: str) -> str:
    return (base_url or "").rstrip("/") + "/" + path.lstrip("/")


def _headers(api_key: str) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    return headers


def _error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return (response.text or "").strip()[:800] or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or json.dumps(error))[:800]
        if isinstance(error, str):
            return error[:800]
        detail = payload.get("message") or payload.get("detail")
        if detail:
            return str(detail)[:800]
    return json.dumps(payload, ensure_ascii=False)[:800]


def _ensure_base(base_url: str) -> str:
    if not (base_url or "").strip():
        raise ProviderError("Base URL provider belum diisi.")
    return base_url


def normalize_model(item: Dict[str, Any], kind: str = "chat") -> Optional[Dict[str, Any]]:
    model_id = item.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
    return {
        "id": model_id.strip(),
        "owned_by": str(item.get("owned_by") or ""),
        "kind": str(item.get("kind") or kind or "chat"),
        "context_length": int(
            item.get("context_length") or capabilities.get("contextWindow") or 0
        ),
        "max_output": int(
            item.get("max_completion_tokens") or capabilities.get("maxOutput") or 0
        ),
        "vision": bool(capabilities.get("vision")),
        "tools": bool(capabilities.get("tools")),
        "reasoning": bool(capabilities.get("reasoning")),
    }


def normalize_models(items: Iterable[Any], kind: str = "chat") -> List[Dict[str, Any]]:
    seen = set()
    models = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        normalized = normalize_model(item, kind)
        if not normalized or normalized["id"] in seen:
            continue
        seen.add(normalized["id"])
        models.append(normalized)
    models.sort(key=lambda model: model["id"].lower())
    return models


async def _get_models(base_url: str, api_key: str, kind: Optional[str]) -> List[Dict[str, Any]]:
    path = "models" if not kind or kind == "chat" else f"models/{kind}"
    try:
        async with httpx.AsyncClient(timeout=MODELS_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(_join(base_url, path), headers=_headers(api_key))
    except httpx.HTTPError as exc:
        raise ProviderError(f"Tidak bisa menghubungi provider: {exc}") from exc

    if response.status_code >= 400:
        raise ProviderError(
            f"Provider menolak permintaan model ({response.status_code}): {_error_text(response)}",
            response.status_code,
        )
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderError(f"Respons /{path} bukan JSON yang valid.") from exc

    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ProviderError(f"Respons /{path} tidak memuat daftar model.")

    models = normalize_models(items, kind or "chat")
    if not models:
        raise ProviderError(f"Provider tidak mengembalikan model {kind or 'chat'} apa pun.")
    return models


async def fetch_models(base_url: str, api_key: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    _ensure_base(base_url)
    return await _get_models(base_url, api_key, kind)


async def fetch_catalog(base_url: str, api_key: str) -> Dict[str, Any]:
    """Ambil semua jenis model. Chat wajib berhasil, jenis lain opsional."""
    _ensure_base(base_url)
    kinds = ("chat",) + MODEL_KINDS
    results = await asyncio.gather(
        *(_get_models(base_url, api_key, None if kind == "chat" else kind) for kind in kinds),
        return_exceptions=True,
    )

    catalog: Dict[str, List[Dict[str, Any]]] = {}
    errors: Dict[str, str] = {}
    for kind, result in zip(kinds, results):
        if isinstance(result, Exception):
            catalog[kind] = []
            if kind == "chat":
                raise result
            errors[kind] = getattr(result, "message", str(result))
        else:
            catalog[kind] = result
    return {"catalog": catalog, "errors": errors}


# --------------------------------------------------------------------- chat


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def _normalize_tool_calls(raw: Any) -> List[Dict[str, Any]]:
    calls = []
    if not isinstance(raw, list):
        return calls
    for item in raw:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)
        calls.append(
            {
                "id": str(item.get("id") or f"call_{len(calls)}"),
                "type": "function",
                "function": {"name": name, "arguments": arguments or "{}"},
            }
        )
    return calls


async def chat_message(
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Kirim percakapan dan kembalikan pesan asisten (teks + tool_calls)."""
    if not model:
        raise ProviderError("Model AI belum dipilih.")
    body: Dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if max_tokens:
        body["max_tokens"] = int(max_tokens)
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(
                _join(base_url, "chat/completions"),
                headers={**_headers(api_key), "Content-Type": "application/json"},
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise ProviderError("Provider tidak merespons sebelum timeout.") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"Tidak bisa menghubungi provider: {exc}") from exc

    if response.status_code >= 400:
        raise ProviderError(
            f"Provider error ({response.status_code}): {_error_text(response)}",
            response.status_code,
        )
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderError("Respons chat bukan JSON yang valid.") from exc

    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ProviderError("Provider tidak mengembalikan pilihan jawaban.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    if not isinstance(message, dict):
        message = {}
    text = _content_text(message.get("content")).strip()
    if not text:
        text = _content_text(message.get("reasoning_content")).strip()
    return {"text": text, "tool_calls": _normalize_tool_calls(message.get("tool_calls"))}


async def chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
) -> str:
    result = await chat_message(base_url, api_key, model, messages, max_tokens, tools)
    if result["text"]:
        return result["text"]
    if result["tool_calls"]:
        return ""
    return "_(model tidak mengembalikan teks jawaban)_"


# ------------------------------------------------------------------- images


def _image_from_payload(payload: Any) -> Optional[bytes]:
    if not isinstance(payload, dict):
        return None
    items = payload.get("data")
    if not isinstance(items, list) or not items:
        return None
    first = items[0] if isinstance(items[0], dict) else {}
    encoded = first.get("b64_json")
    if isinstance(encoded, str) and encoded:
        try:
            return base64.b64decode(encoded)
        except (ValueError, TypeError):
            return None
    return None


async def generate_image(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    size: str = "1024x1024",
) -> Dict[str, Any]:
    """Buat gambar, kembalikan bytes + mime. Mendukung balasan biner maupun JSON."""
    _ensure_base(base_url)
    if not model:
        raise ProviderError("Model gambar belum dipilih.")
    body = {"model": model, "prompt": prompt, "n": 1, "size": size or "1024x1024"}
    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(
                _join(base_url, "images/generations"),
                headers={**_headers(api_key), "Content-Type": "application/json"},
                params={"response_format": "binary"},
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise ProviderError("Pembuatan gambar melebihi timeout.") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"Gagal membuat gambar: {exc}") from exc

    if response.status_code >= 400:
        raise ProviderError(_error_text(response), response.status_code)

    content_type = (response.headers.get("content-type") or "").lower()
    if content_type.startswith("image/"):
        return {"data": response.content, "mime": content_type, "model": model}

    data = _image_from_payload(_safe_json(response))
    if data:
        return {"data": data, "mime": "image/png", "model": model}

    url = None
    payload = _safe_json(response)
    if isinstance(payload, dict) and isinstance(payload.get("data"), list) and payload["data"]:
        first = payload["data"][0]
        if isinstance(first, dict):
            url = first.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT, follow_redirects=True) as client:
            image = await client.get(url)
        if image.status_code < 400 and image.content:
            return {
                "data": image.content,
                "mime": (image.headers.get("content-type") or "image/png").split(";")[0],
                "model": model,
            }
    raise ProviderError("Provider tidak mengembalikan gambar.")


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return None


# -------------------------------------------------------------------- audio


async def speech(
    base_url: str,
    api_key: str,
    model: str,
    text: str,
) -> Dict[str, Any]:
    """Text-to-speech; mengembalikan bytes audio siap diputar."""
    _ensure_base(base_url)
    if not model:
        raise ProviderError("Model TTS belum dipilih.")
    body = {"model": model, "input": text}
    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(
                _join(base_url, "audio/speech"),
                headers={**_headers(api_key), "Content-Type": "application/json"},
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise ProviderError("Text-to-speech melebihi timeout.") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"Gagal membuat suara: {exc}") from exc

    if response.status_code >= 400:
        raise ProviderError(_error_text(response), response.status_code)
    if not response.content:
        raise ProviderError("Provider tidak mengembalikan audio.")

    content_type = (response.headers.get("content-type") or "audio/mpeg").split(";")[0]
    if content_type.startswith("application/json"):
        payload = _safe_json(response) or {}
        encoded = payload.get("audio") if isinstance(payload, dict) else None
        if not encoded:
            raise ProviderError("Respons TTS tidak memuat audio.")
        return {"data": base64.b64decode(encoded), "mime": "audio/mpeg"}
    return {"data": response.content, "mime": content_type}


async def transcribe(
    base_url: str,
    api_key: str,
    model: str,
    filename: str,
    audio: bytes,
    language: str = "",
) -> str:
    """Speech-to-text dari bytes audio."""
    _ensure_base(base_url)
    if not model:
        raise ProviderError("Model STT belum dipilih.")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    data = {"model": model, "response_format": "json"}
    if language:
        data["language"] = language
    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(
                _join(base_url, "audio/transcriptions"),
                headers=headers,
                data=data,
                files={"file": (filename or "audio.wav", audio, "audio/wav")},
            )
    except httpx.TimeoutException as exc:
        raise ProviderError("Transkripsi melewati batas waktu.") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"Gagal transkripsi: {exc}") from exc

    if response.status_code >= 400:
        raise ProviderError(_error_text(response), response.status_code)
    payload = _safe_json(response)
    if isinstance(payload, dict):
        return str(payload.get("text") or "").strip()
    return (response.text or "").strip()
