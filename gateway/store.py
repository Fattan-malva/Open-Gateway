"""Penyimpanan konfigurasi Open Gateway (multi-provider & multi-gateway)."""
import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import CONFIG_FILE
from .gateway_types import GATEWAY_TYPES, gateway_type
from provider.registry import PROVIDER_TYPES, provider_type

DEFAULT_SYSTEM_PROMPT = (
    "Kamu adalah asisten AI di Discord. Jawab dengan jelas, ringkas, dan ramah. "
    "Gunakan bahasa yang sama dengan pengguna."
)

SCHEMA_VERSION = 2

GATEWAY_DEFAULTS = {
    "discord_token": "",
    "provider_id": "",
    "model": "",
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "trigger": "mention",
    "prefix": "!ai",
    "channel_id": "",
    "max_history": 12,
    "max_tokens": 2048,
    "internet_enabled": True,
    "web_search_model": "",
    "web_fetch_model": "",
    "web_max_results": 5,
    "image_enabled": True,
    "image_model": "",
    "image_size": "1024x1024",
    "image_fallback": True,
    "image_token": "",
    "voice_enabled": False,
    "voice_channel_id": "",
    "voice_autojoin": True,
    "voice_listen": True,
    "voice_language": "id",
    "tts_model": "",
    "stt_model": "",
}

SERVER_DEFAULTS = {"host": "127.0.0.1", "port": 8787, "open_browser": True}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _defaults() -> Dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "server": copy.deepcopy(SERVER_DEFAULTS),
        "providers": [],
        "gateways": [],
    }


def _merge(default: Any, value: Any) -> Any:
    if isinstance(default, dict):
        base = copy.deepcopy(default)
        if isinstance(value, dict):
            for key, item in value.items():
                if key in base:
                    base[key] = _merge(base[key], item)
                else:
                    base[key] = copy.deepcopy(item)
        return base
    return copy.deepcopy(value)


def _normalize_provider(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    kind = raw.get("type")
    if kind not in PROVIDER_TYPES:
        kind = "nine_router"
    meta = provider_type(kind)
    base = {
        "id": raw.get("id") or _new_id("prov"),
        "type": kind,
        "name": raw.get("name") or meta["name"],
        "base_url": raw.get("base_url") or meta["default_base_url"],
        "api_key": raw.get("api_key") or "",
        "models": [],
        "catalog": {},
        "updated_at": raw.get("updated_at"),
    }
    merged = _merge(base, raw)
    merged["id"] = str(merged.get("id") or _new_id("prov"))
    merged["type"] = merged.get("type") if merged.get("type") in PROVIDER_TYPES else kind
    merged["name"] = str(merged.get("name") or provider_type(merged["type"])["name"])
    if not isinstance(merged.get("catalog"), dict):
        merged["catalog"] = {}
    catalog = {str(k): list(v or []) for k, v in merged["catalog"].items()}
    if "chat" not in catalog:
        catalog["chat"] = list(merged.get("models") or [])
    merged["catalog"] = catalog
    if not merged.get("models"):
        merged["models"] = list(catalog.get("chat") or [])
    return merged


def _normalize_gateway(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    kind = raw.get("type")
    if kind not in GATEWAY_TYPES:
        kind = "discord"
    base = {
        "id": raw.get("id") or _new_id("gw"),
        "type": kind,
        "name": raw.get("name") or gateway_type(kind)["name"],
        "enabled": bool(raw.get("enabled", True)),
        "provider_id": raw.get("provider_id") or "",
    }
    base.update(copy.deepcopy(GATEWAY_DEFAULTS))
    merged = _merge(base, raw)
    merged["id"] = str(merged.get("id") or _new_id("gw"))
    merged["type"] = merged.get("type") if merged.get("type") in GATEWAY_TYPES else kind
    merged["name"] = str(merged.get("name") or gateway_type(merged["type"])["name"])
    merged["enabled"] = bool(merged.get("enabled", True))
    merged["provider_id"] = str(merged.get("provider_id") or "")
    # pastikan field gateway ada
    for key, value in GATEWAY_DEFAULTS.items():
        if key not in merged or merged.get(key) is None:
            merged[key] = copy.deepcopy(value)
    if kind == "whatsapp":
        merged["enabled"] = False
    return merged


def _migrate(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return _defaults()
    # v2
    if isinstance(raw.get("providers"), list) and isinstance(raw.get("gateways"), list):
        data = _defaults()
        data["version"] = SCHEMA_VERSION
        data["server"] = _merge(SERVER_DEFAULTS, raw.get("server") or {})
        data["providers"] = [_normalize_provider(item) for item in raw.get("providers") if isinstance(item, dict)]
        data["gateways"] = [_normalize_gateway(item) for item in raw.get("gateways") if isinstance(item, dict)]
        return data
    # v1 -> v2
    data = _defaults()
    data["server"] = _merge(SERVER_DEFAULTS, raw.get("server") or {})
    provider_id = ""
    if isinstance(raw.get("provider"), dict):
        provider = _normalize_provider(raw["provider"])
        provider["type"] = provider.get("type") or "nine_router"
        data["providers"] = [provider]
        provider_id = provider["id"]
    if isinstance(raw.get("gateway"), dict):
        gateway = _normalize_gateway(raw["gateway"])
        if gateway.get("provider_id") is None:
            gateway["provider_id"] = provider_id
        if not gateway.get("provider_id"):
            gateway["provider_id"] = provider_id
        data["gateways"] = [gateway]
    return data


class ConfigStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or CONFIG_FILE)
        self._lock = threading.RLock()

    def _load(self) -> Dict[str, Any]:
        # Jangan otomatis tuliskan config.json bila belum ada: biarkan kosong
        # hingga pengguna menambah provider/gateway lewat UI.
        if not self.path.exists():
            return _defaults()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _defaults()
        if not isinstance(raw, dict):
            return _defaults()
        data = _migrate(raw)
        # simpan bila format lama atau belum v2
        if raw.get("version") != SCHEMA_VERSION or "providers" not in raw or "gateways" not in raw:
            self._save(data)
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return self._load()

    def ensure(self) -> Path:
        self.get()
        return self.path

    # ---------- server ----------

    def get_server(self) -> Dict[str, Any]:
        return self.get().get("server") or copy.deepcopy(SERVER_DEFAULTS)

    def update_server(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            server = data.get("server") or copy.deepcopy(SERVER_DEFAULTS)
            for key, value in (changes or {}).items():
                if value is not None:
                    server[key] = value
            data["server"] = server
            self._save(data)
            return server

    # ---------- providers ----------

    def list_providers(self) -> List[Dict[str, Any]]:
        return list(self.get().get("providers") or [])

    def get_provider(self, provider_id: Optional[str] = None) -> Dict[str, Any]:
        providers = self.list_providers()
        if provider_id:
            for item in providers:
                if item.get("id") == provider_id:
                    return item
        return providers[0] if providers else {}

    def add_provider(self, kind: str, name: Optional[str] = None, base_url: Optional[str] = None, api_key: Optional[str] = None) -> Dict[str, Any]:
        if kind not in PROVIDER_TYPES:
            kind = "openai"
        meta = provider_type(kind)
        provider = {
            "id": _new_id("prov"),
            "type": kind,
            "name": name or meta["name"],
            "base_url": base_url or meta["default_base_url"],
            "api_key": api_key or "",
            "models": [],
            "catalog": {},
            "updated_at": None,
        }
        provider = _normalize_provider(provider)
        with self._lock:
            data = self._load()
            data.setdefault("providers", []).append(provider)
            self._save(data)
        return provider

    def update_provider(self, provider_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            providers = data.setdefault("providers", [])
            target = None
            for item in providers:
                if item.get("id") == provider_id:
                    target = item
                    break
            if target is None:
                raise KeyError(f"Provider tidak ditemukan: {provider_id}")
            for key in ("name", "base_url", "api_key"):
                value = (changes or {}).get(key, None)
                if value is not None:
                    target[key] = value
            # keep catalog as-is unless provided
            if (changes or {}).get("catalog") is not None:
                target["catalog"] = (changes or {}).get("catalog")
            if (changes or {}).get("models") is not None:
                target["models"] = (changes or {}).get("models")
            if (changes or {}).get("updated_at") is not None:
                target["updated_at"] = (changes or {}).get("updated_at")
            target["type"] = target.get("type") if target.get("type") in PROVIDER_TYPES else "openai"
            self._save(data)
            return target

    def set_models(self, provider_id: str, models: list, updated_at: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            target = None
            for item in data.get("providers", []):
                if item.get("id") == provider_id:
                    target = item
                    break
            if target is None:
                raise KeyError(f"Provider tidak ditemukan: {provider_id}")
            target["models"] = list(models)
            catalog = target.get("catalog")
            if not isinstance(catalog, dict):
                catalog = {}
            catalog["chat"] = list(models)
            target["catalog"] = catalog
            target["updated_at"] = int(updated_at or time.time())
            self._save(data)
            return target

    def set_catalog(self, provider_id: str, catalog: Dict[str, list], updated_at: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            target = None
            for item in data.get("providers", []):
                if item.get("id") == provider_id:
                    target = item
                    break
            if target is None:
                raise KeyError(f"Provider tidak ditemukan: {provider_id}")
            clean = {str(k): list(v or []) for k, v in (catalog or {}).items()}
            target["catalog"] = clean
            target["models"] = clean.get("chat", [])
            target["updated_at"] = int(updated_at or time.time())
            self._save(data)
            return target

    def delete_provider(self, provider_id: str) -> bool:
        with self._lock:
            data = self._load()
            providers = data.get("providers", [])
            before = len(providers)
            data["providers"] = [p for p in providers if p.get("id") != provider_id]
            # reassign gateways
            remaining_ids = [p.get("id") for p in data["providers"]]
            fallback = remaining_ids[0] if remaining_ids else ""
            for gateway in data.get("gateways", []):
                if gateway.get("provider_id") == provider_id:
                    gateway["provider_id"] = fallback
            self._save(data)
            return len(data["providers"]) < before

    # ---------- gateways ----------

    def list_gateways(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        items = list(self.get().get("gateways") or [])
        if kind:
            items = [g for g in items if g.get("type") == kind]
        return items

    def get_gateway(self, gateway_id: Optional[str] = None) -> Dict[str, Any]:
        gateways = self.list_gateways()
        if gateway_id:
            for item in gateways:
                if item.get("id") == gateway_id:
                    return item
        return gateways[0] if gateways else {}

    def add_gateway(self, kind: str, name: Optional[str] = None, provider_id: Optional[str] = None) -> Dict[str, Any]:
        if kind not in GATEWAY_TYPES:
            kind = "discord"
        meta = gateway_type(kind)
        gateway = {
            "id": _new_id("gw"),
            "type": kind,
            "name": name or meta["name"],
            "enabled": kind != "whatsapp",
            "provider_id": provider_id or "",
        }
        gateway.update(copy.deepcopy(GATEWAY_DEFAULTS))
        gateway = _normalize_gateway(gateway)
        if provider_id:
            gateway["provider_id"] = provider_id
        with self._lock:
            data = self._load()
            data.setdefault("gateways", []).append(gateway)
            self._save(data)
        return gateway

    def update_gateway(self, gateway_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            gateways = data.setdefault("gateways", [])
            target = None
            for item in gateways:
                if item.get("id") == gateway_id:
                    target = item
                    break
            if target is None:
                raise KeyError(f"Gateway tidak ditemukan: {gateway_id}")
            for key, value in (changes or {}).items():
                if value is not None and key in target:
                    target[key] = value
            # allow name/enabled/provider_id
            for key in ("name", "enabled", "provider_id"):
                if key in (changes or {}) and (changes or {}).get(key) is not None:
                    target[key] = (changes or {}).get(key)
            self._save(data)
            return target

    def delete_gateway(self, gateway_id: str) -> bool:
        with self._lock:
            data = self._load()
            gateways = data.get("gateways", [])
            before = len(gateways)
            data["gateways"] = [g for g in gateways if g.get("id") != gateway_id]
            self._save(data)
            return len(data["gateways"]) < before

    # ---------- public views ----------

    @staticmethod
    def provider_public(provider: Dict[str, Any]) -> Dict[str, Any]:
        provider = provider or {}
        models = provider.get("models") or []
        catalog = provider.get("catalog")
        if not isinstance(catalog, dict):
            catalog = {}
        catalog = {str(k): list(v or []) for k, v in catalog.items()}
        catalog.setdefault("chat", models)
        counts = {str(k): len(v or []) for k, v in catalog.items()}
        api_key = provider.get("api_key") or ""
        return {
            "id": provider.get("id") or "",
            "type": provider.get("type") or "openai",
            "name": provider.get("name") or provider_type(provider.get("type") or "openai")["name"],
            "base_url": provider.get("base_url") or "",
            "api_key": "",
            "api_key_set": bool(api_key),
            "api_key_hint": ("..." + api_key[-4:]) if len(api_key) >= 4 else "",
            "models": models,
            "model_count": len(models),
            "catalog": catalog,
            "catalog_counts": counts,
            "updated_at": provider.get("updated_at"),
            "configured": bool(provider.get("base_url") and api_key),
            "type_info": provider_type(provider.get("type") or "openai"),
        }

    @staticmethod
    def gateway_public(gateway: Dict[str, Any]) -> Dict[str, Any]:
        gateway = gateway or {}
        token = (gateway.get("discord_token") or "").strip()
        image_token = (gateway.get("image_token") or "").strip()
        public = {
            "id": gateway.get("id") or "",
            "type": gateway.get("type") or "discord",
            "name": gateway.get("name") or gateway_type(gateway.get("type") or "discord")["name"],
            "enabled": bool(gateway.get("enabled", True)),
            "provider_id": gateway.get("provider_id") or "",
            "type_info": gateway_type(gateway.get("type") or "discord"),
            "model": gateway.get("model") or "",
            "system_prompt": gateway.get("system_prompt") or "",
            "trigger": gateway.get("trigger") or "mention",
            "prefix": gateway.get("prefix") or "!ai",
            "channel_id": gateway.get("channel_id") or "",
            "max_history": int(gateway.get("max_history") or 12),
            "max_tokens": int(gateway.get("max_tokens") or 2048),
            "internet_enabled": bool(gateway.get("internet_enabled", True)),
            "web_search_model": gateway.get("web_search_model") or "",
            "web_fetch_model": gateway.get("web_fetch_model") or "",
            "web_max_results": int(gateway.get("web_max_results") or 5),
            "image_enabled": bool(gateway.get("image_enabled", True)),
            "image_model": gateway.get("image_model") or "",
            "image_size": gateway.get("image_size") or "1024x1024",
            "image_fallback": bool(gateway.get("image_fallback", True)),
            "image_token_set": bool(image_token),
            "image_token_hint": ("..." + image_token[-4:]) if len(image_token) >= 4 else "",
            "voice_enabled": bool(gateway.get("voice_enabled", False)),
            "voice_channel_id": gateway.get("voice_channel_id") or "",
            "voice_autojoin": bool(gateway.get("voice_autojoin", True)),
            "voice_listen": bool(gateway.get("voice_listen", True)),
            "voice_language": gateway.get("voice_language") or "id",
            "tts_model": gateway.get("tts_model") or "",
            "stt_model": gateway.get("stt_model") or "",
            "discord_token_set": bool(token),
            "discord_token_hint": ("..." + token[-4:]) if len(token) >= 4 else "",
        }
        return public


_store: Optional[ConfigStore] = None


def get_store() -> ConfigStore:
    global _store
    if _store is None:
        _store = ConfigStore()
    return _store
