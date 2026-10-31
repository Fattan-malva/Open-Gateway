"""Gambar dan suara: pembuatan gambar, text-to-speech, dan transkripsi."""
import os
import random
import re
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

import provider

IMAGE_SIZES = ("1024x1024", "1792x1024", "1024x1792", "512x512")
DEFAULT_IMAGE_SIZE = "1024x1024"

EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
}


def extension_for(mime: str, fallback: str = "bin") -> str:
    return EXTENSIONS.get((mime or "").lower().split(";")[0].strip(), fallback)


def normalize_size(size: str) -> str:
    value = (size or "").strip().lower().replace("×", "x")
    return value if value in IMAGE_SIZES else DEFAULT_IMAGE_SIZE


def pollinations_token(settings: Optional[Dict[str, Any]] = None) -> str:
    """Token opsional agar batas gratis pollinations tidak 1 gambar / 45 detik."""
    settings = settings or {}
    token = (settings.get("image_token") or "").strip()
    if token:
        return token
    return (
        os.getenv("OG_POLLINATIONS_TOKEN", "").strip()
        or os.getenv("POLLINATIONS_TOKEN", "").strip()
    )


def _pollinations_error(response: httpx.Response) -> str:
    """Ubah balasan error pollinations jadi pesan yang bisa dibaca pengguna."""
    message = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("error")
        if isinstance(detail, dict):
            message = str(detail.get("message") or "")
        elif detail:
            message = str(detail)
        elif payload.get("message"):
            message = str(payload["message"])
    wait = re.search(r"retry after (\d+)\s*s", message, re.I)
    if not wait and response.status_code in (402, 429, 503):
        wait = re.match(r"\s*(\d+)\s*$", response.headers.get("retry-after", "") or "")
    if "free limit" in message.lower() or response.status_code == 402:
        if wait:
            return f"Batas gambar gratis tercapai. Coba lagi dalam {wait.group(1)} detik."
        return "Batas gambar gratis tercapai. Coba lagi sekitar 45 detik lagi."
    if response.status_code == 503:
        return "Layanan gambar cadangan sedang sibuk (HTTP 503). Coba lagi sebentar lagi."
    return f"Gambar gagal dibuat (HTTP {response.status_code})."


async def _pollinations(prompt: str, size: str, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    width, height = (normalize_size(size).split("x") + ["1024", "1024"])[:2]
    url = (
        "https://image.pollinations.ai/prompt/"
        + quote(prompt[:1200], safe="")
        + f"?width={width}&height={height}&nologo=true&seed={random.randint(1, 2**31 - 1)}"
    )
    token = pollinations_token(settings)
    if token:
        url += f"&token={quote(token, safe='')}"
    headers = {"User-Agent": "OpenGateway/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=180, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise provider.ProviderError(f"Gambar gagal dibuat: {exc}") from exc
    if response.status_code >= 400 or not response.content:
        raise provider.ProviderError(_pollinations_error(response))
    mime = (response.headers.get("content-type") or "image/jpeg").split(";")[0]
    if not mime.startswith("image/"):
        raise provider.ProviderError("Layanan gambar tidak mengembalikan gambar.")
    return {"data": response.content, "mime": mime, "model": "pollinations", "source": "fallback"}


async def generate_image(
    provider_cfg: Dict[str, Any],
    settings: Dict[str, Any],
    prompt: str,
    log=None,
) -> Dict[str, Any]:
    """Buat gambar lewat 9Router; bila gagal, pakai layanan gratis sebagai cadangan."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise provider.ProviderError("Prompt gambar kosong.")
    model = (settings.get("image_model") or "").strip()
    size = normalize_size(settings.get("image_size") or DEFAULT_IMAGE_SIZE)
    base_url = provider_cfg.get("base_url") or ""
    api_key = provider_cfg.get("api_key") or ""

    error = None
    if model:
        try:
            result = await provider.generate_image(provider_cfg, model, prompt, size)
            result["source"] = (provider_cfg.get("type") or "provider")
            return result
        except provider.ProviderError as exc:
            error = exc
            if log:
                log("warn", f"Gambar via {model} gagal: {exc.message[:200]}")

    if settings.get("image_fallback", True):
        result = await _pollinations(prompt, size, settings)
        if log:
            log("info", "Gambar dibuat lewat cadangan gratis (pollinations).")
        return result

    raise error or provider.ProviderError("Model gambar belum dipilih.")


CODE_BLOCK = re.compile(r"(?is)```.*?```")
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
URL_PATTERN = re.compile(r"https?://\S+")
MARKDOWN_CHARS = re.compile(r"[*_`#>|]+")


def clean_for_speech(text: str, limit: int = 700) -> str:
    """Rapikan jawaban agar enak dibacakan (tanpa markdown/kode/URL panjang)."""
    value = CODE_BLOCK.sub(" kode program dilewati. ", text or "")
    value = MARKDOWN_LINK.sub(r"\1", value)
    value = URL_PATTERN.sub(" tautan ", value)
    value = MARKDOWN_CHARS.sub(" ", value)
    value = re.sub(r"^\s*[-•]\s*", "", value, flags=re.M)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        cut = value.rfind(". ", 0, limit)
        value = value[: cut + 1] if cut > limit // 2 else value[:limit].rstrip() + "…"
    return value


async def synthesize(
    provider_cfg: Dict[str, Any],
    settings: Dict[str, Any],
    text: str,
) -> Optional[Dict[str, Any]]:
    model = (settings.get("tts_model") or "").strip()
    if not model:
        return None
    spoken = clean_for_speech(text)
    if not spoken:
        return None
    result = await provider.speech(
        provider_cfg,
        model,
        spoken,
    )
    result["text"] = spoken
    return result


async def transcribe_audio(
    provider_cfg: Dict[str, Any],
    settings: Dict[str, Any],
    audio: bytes,
    filename: str = "audio.wav",
) -> str:
    model = (settings.get("stt_model") or "").strip()
    if not model:
        raise provider.ProviderError("Model STT belum dipilih.")
    language = (settings.get("voice_language") or "").strip()
    return await provider.transcribe(
        provider_cfg,
        model,
        filename,
        audio,
        language,
    )
