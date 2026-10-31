"""Klien provider AI OpenAI-compatible: chat, model, web, gambar, dan audio."""
from . import client, registry
from .client import (
    ProviderError,
    normalize_model,
    normalize_models,
    fetch_models,
    chat_message,
    chat,
    generate_image,
    speech,
    transcribe,
)
from .registry import (
    PROVIDER_TYPES,
    provider_type,
    provider_types_public,
    fetch_catalog,
    chat_message as registry_chat_message,
    generate_image as registry_generate_image,
    speech as registry_speech,
    transcribe as registry_transcribe,
    web_search,
    web_fetch,
)

# Alias agar modul lain bisa memakai cfg provider langsung.
chat_message = registry_chat_message
generate_image = registry_generate_image
speech = registry_speech
transcribe = registry_transcribe

__all__ = [
    "ProviderError",
    "normalize_model",
    "normalize_models",
    "fetch_models",
    "chat",
    "chat_message",
    "generate_image",
    "speech",
    "transcribe",
    "fetch_catalog",
    "web_search",
    "web_fetch",
    "PROVIDER_TYPES",
    "provider_type",
    "provider_types_public",
]
