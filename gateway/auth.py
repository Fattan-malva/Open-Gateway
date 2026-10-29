"""Penyimpanan dan validasi API key gateway."""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional, Tuple

from fastapi import Request

from .config import API_KEYS_FILE
from .errors import ApiError


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApiKeyStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or API_KEYS_FILE)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"keys": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"keys": []}
        if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
            return {"keys": []}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def create(self, name: str = "default") -> Tuple[dict, str]:
        raw = "sk-ds-" + secrets.token_urlsafe(32)
        record = {
            "id": "key_" + secrets.token_hex(4),
            "name": (name or "default").strip(),
            "prefix": raw[:12] + "..." + raw[-4:],
            "hash": _hash_key(raw),
            "created_at": int(time.time()),
            "last_used_at": None,
            "revoked": False,
        }
        data = self._load()
        data["keys"].append(record)
        self._save(data)
        return self.public(record), raw

    def list_keys(self) -> list:
        return [self.public(rec) for rec in self._load()["keys"]]

    def has_active_keys(self) -> bool:
        return any(not rec.get("revoked") for rec in self._load()["keys"])

    def revoke(self, identifier: str) -> bool:
        identifier = (identifier or "").strip()
        if not identifier:
            return False
        data = self._load()
        changed = False
        for rec in data["keys"]:
            if rec.get("revoked"):
                continue
            if identifier in (rec.get("id"), rec.get("prefix")) or rec.get("prefix", "").startswith(identifier):
                rec["revoked"] = True
                changed = True
        if changed:
            self._save(data)
        return changed

    def verify(self, raw: str) -> Optional[dict]:
        if not raw:
            return None
        digest = _hash_key(raw)
        data = self._load()
        now = int(time.time())
        for rec in data["keys"]:
            if rec.get("revoked"):
                continue
            stored = rec.get("hash", "")
            if stored and secrets.compare_digest(stored, digest):
                if now - int(rec.get("last_used_at") or 0) > 60:
                    rec["last_used_at"] = now
                    try:
                        self._save(data)
                    except OSError:
                        pass
                return self.public(rec)
        return None

    @staticmethod
    def public(record: dict) -> dict:
        return {k: v for k, v in record.items() if k != "hash"}


_store: Optional[ApiKeyStore] = None


def get_store() -> ApiKeyStore:
    global _store
    if _store is None:
        _store = ApiKeyStore()
    return _store


def _extract_key(request: Request) -> str:
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("x-api-key") or "").strip()


async def require_api_key(request: Request) -> dict:
    raw = _extract_key(request)
    if not raw:
        raise ApiError(
            401,
            "Missing API key. Buat key di tab API Keys pada web open-masking.",
            error_type="authentication_error",
            code="missing_api_key",
        )
    record = get_store().verify(raw)
    if not record:
        raise ApiError(
            401,
            "Invalid or revoked API key.",
            error_type="authentication_error",
            code="invalid_api_key",
        )
    return record
