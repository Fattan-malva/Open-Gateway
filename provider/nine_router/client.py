"""Klien khusus 9Router."""
import asyncio
import json
from typing import Any, Dict, List

import httpx

from gateway import config
from .. import client

MODEL_KINDS = ("image", "tts", "stt", "web", "embedding")


async def fetch_catalog(base_url: str, api_key: str) -> Dict[str, Any]:
    client._ensure_base(base_url)
    kinds = ("chat",) + MODEL_KINDS
    results = await asyncio.gather(
        *(client.fetch_models(base_url, api_key, None if kind == "chat" else kind) for kind in kinds),
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


async def web_search(base_url: str, api_key: str, model: str, query: str, max_results: int = 5) -> Dict[str, Any]:
    client._ensure_base(base_url)
    body = {"model": model or "tavily", "query": query, "max_results": int(max_results)}
    try:
        async with httpx.AsyncClient(timeout=config.MODELS_TIMEOUT, follow_redirects=True) as c:
            response = await c.post(
                client._join(base_url, "search"),
                headers={**client._headers(api_key), "Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise client.ProviderError(f"Pencarian gagal: {exc}") from exc
    if response.status_code >= 400:
        raise client.ProviderError(client._error_text(response), response.status_code)
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise client.ProviderError("Respons pencarian bukan JSON.") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    return {"results": results or [], "answer": payload.get("answer") if isinstance(payload, dict) else None}


async def web_fetch(base_url: str, api_key: str, model: str, url: str, max_characters: int = 8000) -> Dict[str, Any]:
    client._ensure_base(base_url)
    body = {
        "model": model or "fetch-combo",
        "url": url,
        "format": "markdown",
        "max_characters": int(max_characters),
    }
    try:
        async with httpx.AsyncClient(timeout=config.PROVIDER_TIMEOUT, follow_redirects=True) as c:
            response = await c.post(
                client._join(base_url, "web/fetch"),
                headers={**client._headers(api_key), "Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise client.ProviderError(f"Gagal mengambil halaman: {exc}") from exc
    if response.status_code >= 400:
        raise client.ProviderError(client._error_text(response), response.status_code)
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise client.ProviderError("Respons fetch bukan JSON.") from exc
    content = payload.get("content") if isinstance(payload, dict) else None
    text = ""
    if isinstance(content, dict):
        text = str(content.get("text") or "")
    elif isinstance(content, str):
        text = content
    return {"title": str(payload.get("title") or url), "text": text, "url": str(payload.get("url") or url)}
