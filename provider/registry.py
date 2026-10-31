"""Registry jenis provider AI dan pengambilan katalog/web."""
import asyncio
from typing import Any, Dict, List

from gateway import config
from .nine_router import client as nine_router_client
from . import client


PROVIDER_TYPES: Dict[str, Dict[str, Any]] = {
    "nine_router": {
        "id": "nine_router",
        "name": "9Router",
        "description": "Gateway model OpenAI-compatible khusus 9Router: chat, image, TTS, STT, web, dan embedding.",
        "default_base_url": "http://localhost:20128",
        "icon": "assets/icon-9router.svg",
        "available": True,
        "supports_web": True,
        "kinds": ["chat", "image", "tts", "stt", "web", "embedding"],
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI-compatible",
        "description": "Endpoint OpenAI-compatible umum: /v1/chat/completions dan /v1/models.",
        "default_base_url": "https://api.openai.com/v1",
        "icon": "assets/icon-openai.svg",
        "available": True,
        "supports_web": False,
        "kinds": ["chat", "image", "tts", "stt", "embedding"],
    },
}


def provider_type(kind: str) -> Dict[str, Any]:
    return PROVIDER_TYPES.get(kind, PROVIDER_TYPES["openai"])


def provider_types_public() -> List[Dict[str, Any]]:
    return [
        {
            "id": value["id"],
            "name": value["name"],
            "description": value["description"],
            "default_base_url": value["default_base_url"],
            "icon": value["icon"],
            "available": value["available"],
            "supports_web": value["supports_web"],
            "kinds": list(value["kinds"]),
        }
        for value in PROVIDER_TYPES.values()
    ]


async def fetch_catalog(cfg: Dict[str, Any]) -> Dict[str, Any]:
    kind = (cfg or {}).get("type") or "openai"
    base_url = (cfg or {}).get("base_url") or ""
    api_key = (cfg or {}).get("api_key") or ""
    if kind == "nine_router":
        return await nine_router_client.fetch_catalog(base_url, api_key)
    # OpenAI-compatible: chat wajib, jenis lain opsional (banyak endpoint tidak menyediakannya).
    kinds = ("chat",) + tuple(
        k for k in ("image", "tts", "stt", "embedding") if k in provider_type(kind)["kinds"]
    )
    results = await asyncio.gather(
        *(client.fetch_models(base_url, api_key, None if k == "chat" else k) for k in kinds),
        return_exceptions=True,
    )
    catalog: Dict[str, List[Dict[str, Any]]] = {}
    errors: Dict[str, str] = {}
    for k, result in zip(kinds, results):
        if isinstance(result, Exception):
            catalog[k] = []
            if k == "chat":
                raise result
            errors[k] = getattr(result, "message", str(result))
        else:
            catalog[k] = result
    catalog.setdefault("chat", [])
    return {"catalog": catalog, "errors": errors}


async def chat_message(cfg: Dict[str, Any], model: str, messages, max_tokens=None, tools=None):
    return await client.chat_message(
        (cfg or {}).get("base_url") or "",
        (cfg or {}).get("api_key") or "",
        model,
        messages,
        max_tokens,
        tools,
    )


async def generate_image(cfg: Dict[str, Any], model: str, prompt: str, size: str = "1024x1024"):
    return await client.generate_image(
        (cfg or {}).get("base_url") or "",
        (cfg or {}).get("api_key") or "",
        model,
        prompt,
        size,
    )


async def speech(cfg: Dict[str, Any], model: str, text: str):
    return await client.speech(
        (cfg or {}).get("base_url") or "",
        (cfg or {}).get("api_key") or "",
        model,
        text,
    )


async def transcribe(cfg: Dict[str, Any], model: str, filename: str, audio: bytes, language: str = ""):
    return await client.transcribe(
        (cfg or {}).get("base_url") or "",
        (cfg or {}).get("api_key") or "",
        model,
        filename,
        audio,
        language,
    )


async def web_search(cfg: Dict[str, Any], model: str, query: str, max_results: int = 5):
    if (cfg or {}).get("type") != "nine_router":
        raise client.ProviderError("Provider ini tidak memiliki endpoint pencarian; memakai pencarian lokal.")
    return await nine_router_client.web_search(
        (cfg or {}).get("base_url") or "",
        (cfg or {}).get("api_key") or "",
        model,
        query,
        max_results,
    )


async def web_fetch(cfg: Dict[str, Any], model: str, url: str, max_characters: int = 8000):
    if (cfg or {}).get("type") != "nine_router":
        raise client.ProviderError("Provider ini tidak memiliki endpoint fetch; memakai pembaca halaman bawaan.")
    return await nine_router_client.web_fetch(
        (cfg or {}).get("base_url") or "",
        (cfg or {}).get("api_key") or "",
        model,
        url,
        max_characters,
    )
