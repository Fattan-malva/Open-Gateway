"""Endpoint admin untuk web UI Open Gateway (hanya localhost)."""
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

import provider as providers
from .config import APP_NAME, DATA_DIR, VERSION
from .discord import get_hub
from .discord import cron as cronmod
from .errors import ApiError
from .auth import require_auth
from .gateway_types import GATEWAY_TYPES, gateway_type, gateway_types_public
from .media import IMAGE_SIZES
from .store import get_store

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}

TRIGGERS = {"mention", "all", "prefix"}


async def require_local(request: Request):
    host = (request.client.host if request.client else "") or ""
    if host not in LOCAL_HOSTS:
        raise ApiError(
            403,
            "Halaman admin hanya bisa diakses dari localhost.",
            error_type="permission_error",
            code="forbidden",
        )


class ProviderCreate(BaseModel):
    type: str = "nine_router"
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    fetch: bool = True


class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    fetch: Optional[bool] = True


class GatewayCreate(BaseModel):
    type: str = "discord"
    name: Optional[str] = None
    provider_id: Optional[str] = None


class GatewayUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    provider_id: Optional[str] = None
    discord_token: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    trigger: Optional[str] = None
    prefix: Optional[str] = None
    channel_id: Optional[str] = None
    max_history: Optional[int] = None
    max_tokens: Optional[int] = None
    internet_enabled: Optional[bool] = None
    web_search_model: Optional[str] = None
    web_fetch_model: Optional[str] = None
    web_max_results: Optional[int] = None
    image_enabled: Optional[bool] = None
    image_model: Optional[str] = None
    image_size: Optional[str] = None
    image_fallback: Optional[bool] = None
    image_token: Optional[str] = None
    voice_enabled: Optional[bool] = None
    voice_channel_id: Optional[str] = None
    voice_autojoin: Optional[bool] = None
    voice_listen: Optional[bool] = None
    voice_language: Optional[str] = None
    tts_model: Optional[str] = None
    stt_model: Optional[str] = None
    cron_jobs: Optional[list] = None


class CronJobUpdate(BaseModel):
    schedule: Optional[str] = None
    prompt: Optional[str] = None
    channel_id: Optional[str] = None
    enabled: Optional[bool] = None


def _base_url() -> str:
    server = get_store().get_server()
    host = server.get("host") or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = server.get("port") or 8787
    return f"http://{host}:{port}"


def _clean_base_url(raw: str) -> str:
    value = (raw or "").strip().rstrip("/")
    if not value:
        raise ApiError(400, "Base URL provider wajib diisi.", code="invalid_base_url")
    if not value.startswith(("http://", "https://")):
        raise ApiError(
            400,
            "Base URL harus diawali http:// atau https://.",
            code="invalid_base_url",
        )
    return value


def _provider_cfg(provider: dict) -> dict:
    return provider or {}


def create_router(_bot=None) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_auth)])
    hub = get_hub()

    @router.get("/status")
    async def status():
        store = get_store()
        providers_list = store.list_providers()
        configured = any(
            p.get("base_url") and p.get("api_key") for p in providers_list
        )
        return {
            "app": APP_NAME,
            "version": VERSION,
            "base_url": _base_url(),
            "provider": {
                "configured": configured,
                "count": len(providers_list),
            },
            "gateways": hub.status(),
            "paths": {"data": str(DATA_DIR)},
        }

    # ---------- provider types & providers ----------

    @router.get("/provider/types")
    async def provider_types():
        return {"types": providers.provider_types_public()}

    @router.get("/providers")
    async def list_providers():
        store = get_store()
        return {
            "types": providers.provider_types_public(),
            "providers": [store.provider_public(p) for p in store.list_providers()],
        }

    @router.post("/providers")
    async def create_provider(body: ProviderCreate):
        if body.type not in providers.PROVIDER_TYPES:
            raise ApiError(400, "Tipe provider tidak dikenal.", code="invalid_provider_type")
        meta = providers.provider_type(body.type)
        base_url = body.base_url
        if base_url:
            base_url = _clean_base_url(base_url)
        else:
            base_url = meta["default_base_url"]
        api_key = (body.api_key or "").strip()
        provider = get_store().add_provider(body.type, body.name, base_url, api_key)
        warning = None
        catalog_errors = {}
        if body.fetch and base_url and api_key:
            try:
                result = await providers.fetch_catalog(provider)
                # provider dict from store lacks? fetch_catalog reads cfg fields
                get_store().set_catalog(provider["id"], result["catalog"], int(time.time()))
                catalog_errors = result.get("errors", {})
            except providers.ProviderError as exc:
                warning = exc.message
            except Exception as exc:
                warning = f"Gagal memuat model: {exc}"
        return {
            "provider": get_store().provider_public(get_store().get_provider(provider["id"])),
            "warning": warning,
            "catalog_errors": catalog_errors,
        }

    @router.get("/providers/{provider_id}")
    async def get_provider(provider_id: str):
        provider = get_store().get_provider(provider_id)
        if not provider:
            raise ApiError(404, "Provider tidak ditemukan.", code="provider_not_found")
        return get_store().provider_public(provider)

    @router.post("/providers/{provider_id}")
    async def update_provider(provider_id: str, body: ProviderUpdate):
        store = get_store()
        current = store.get_provider(provider_id)
        if not current:
            raise ApiError(404, "Provider tidak ditemukan.", code="provider_not_found")

        changes = {}
        if body.name is not None:
            changes["name"] = body.name.strip() or current.get("name")
        base_url = current.get("base_url") or ""
        if body.base_url is not None:
            base_url = _clean_base_url(body.base_url)
            changes["base_url"] = base_url
        api_key = (body.api_key or "").strip()
        if api_key:
            changes["api_key"] = api_key
        elif not (current.get("api_key") or "").strip() and not body.base_url:
            pass

        # update base/name/api_key
        store.update_provider(provider_id, changes)

        warning = None
        catalog_errors = {}
        should_fetch = body.fetch if body.fetch is not None else True
        provider_cfg = store.get_provider(provider_id)
        if should_fetch and provider_cfg.get("base_url") and provider_cfg.get("api_key"):
            # fetch only if base_url or api_key changed or forced
            try:
                result = await providers.fetch_catalog(provider_cfg)
                store.set_catalog(provider_id, result["catalog"], int(time.time()))
                catalog_errors = result.get("errors", {})
            except providers.ProviderError as exc:
                warning = exc.message
            except Exception as exc:
                warning = f"Gagal memuat model: {exc}"

        return {
            "provider": store.provider_public(store.get_provider(provider_id)),
            "warning": warning,
            "catalog_errors": catalog_errors,
        }

    @router.post("/providers/{provider_id}/refresh")
    async def refresh_provider(provider_id: str):
        store = get_store()
        provider_cfg = store.get_provider(provider_id)
        if not provider_cfg:
            raise ApiError(404, "Provider tidak ditemukan.", code="provider_not_found")
        base_url = provider_cfg.get("base_url") or ""
        api_key = provider_cfg.get("api_key") or ""
        if not base_url or not api_key:
            raise ApiError(400, "Simpan Base URL dan API key provider terlebih dahulu.", code="provider_not_configured")
        warning = None
        catalog_errors = {}
        try:
            result = await providers.fetch_catalog(provider_cfg)
            store.set_catalog(provider_id, result["catalog"], int(time.time()))
            catalog_errors = result.get("errors", {})
        except providers.ProviderError as exc:
            warning = exc.message
        except Exception as exc:
            warning = f"Gagal memuat model: {exc}"
        return {
            "provider": store.provider_public(store.get_provider(provider_id)),
            "warning": warning,
            "catalog_errors": catalog_errors,
        }

    @router.delete("/providers/{provider_id}")
    async def delete_provider(provider_id: str):
        store = get_store()
        if not store.get_provider(provider_id):
            raise ApiError(404, "Provider tidak ditemukan.", code="provider_not_found")
        store.delete_provider(provider_id)
        return {"deleted": True}

    # ---------- gateway types & gateways ----------

    @router.get("/gateway/types")
    async def gateway_types():
        return {"types": gateway_types_public()}

    @router.get("/gateways")
    async def list_gateways():
        store = get_store()
        result = []
        for gw in store.list_gateways():
            gid = gw.get("id") or ""
            result.append({
                "settings": store.gateway_public(gw),
                "status": hub.status(gid),
            })
        return {
            "types": gateway_types_public(),
            "gateways": result,
        }

    @router.post("/gateways")
    async def create_gateway(body: GatewayCreate):
        if body.type not in GATEWAY_TYPES:
            raise ApiError(400, "Tipe gateway tidak dikenal.", code="invalid_gateway_type")
        meta = gateway_type(body.type)
        if meta.get("available") != "true":
            raise ApiError(400, "Gateway ini segera hadir.", code="gateway_coming_soon")
        store = get_store()
        provider_id = body.provider_id or ""
        if provider_id and not store.get_provider(provider_id):
            raise ApiError(400, "Provider tidak ditemukan.", code="provider_not_found")
        gateway = store.add_gateway(body.type, body.name, provider_id)
        hub.get(gateway["id"])
        return {
            "settings": store.gateway_public(gateway),
            "status": hub.status(gateway["id"]),
        }

    @router.get("/gateways/{gateway_id}")
    async def get_gateway(gateway_id: str):
        store = get_store()
        gateway = store.get_gateway(gateway_id)
        if not gateway or gateway.get("id") != gateway_id:
            raise ApiError(404, "Gateway tidak ditemukan.", code="gateway_not_found")
        return {
            "settings": store.gateway_public(gateway),
            "status": hub.status(gateway_id),
            "logs": hub.logs(gateway_id),
        }

    @router.post("/gateways/{gateway_id}")
    async def update_gateway(gateway_id: str, body: GatewayUpdate):
        store = get_store()
        current = store.get_gateway(gateway_id)
        if not current or current.get("id") != gateway_id:
            raise ApiError(404, "Gateway tidak ditemukan.", code="gateway_not_found")

        changes = {}
        if body.name is not None:
            changes["name"] = body.name.strip() or current.get("name")
        if body.enabled is not None:
            changes["enabled"] = bool(body.enabled)
        if body.provider_id is not None:
            pid = body.provider_id.strip()
            if pid and not store.get_provider(pid):
                raise ApiError(400, "Provider tidak ditemukan.", code="provider_not_found")
            changes["provider_id"] = pid

        token = (body.discord_token or "").strip() if body.discord_token is not None else ""
        # keep existing token if blank
        if body.discord_token is not None and token:
            changes["discord_token"] = token

        if body.model is not None:
            changes["model"] = body.model.strip()
        if body.system_prompt is not None:
            changes["system_prompt"] = body.system_prompt.strip()[:8000]

        trigger = current.get("trigger") or "mention"
        if body.trigger is not None:
            trigger = body.trigger.strip().lower()
            if trigger not in TRIGGERS:
                raise ApiError(400, f"Trigger tidak dikenal: {trigger}", code="invalid_trigger")
        changes["trigger"] = trigger

        prefix = current.get("prefix") or "!ai"
        if body.prefix is not None:
            prefix = body.prefix.strip()
        changes["prefix"] = prefix or "!ai"

        channel_id = current.get("channel_id") or ""
        if body.channel_id is not None:
            channel_id = body.channel_id.strip()
        if channel_id and not channel_id.isdigit():
            raise ApiError(400, "Channel ID harus berupa angka.", code="invalid_channel")
        if trigger == "all" and not channel_id:
            raise ApiError(400, "Mode 'semua pesan' membutuhkan Channel ID agar bot tidak membalas di semua channel.", code="missing_channel")
        changes["channel_id"] = channel_id

        if body.max_history is not None:
            changes["max_history"] = max(2, min(50, int(body.max_history)))
        if body.max_tokens is not None:
            changes["max_tokens"] = max(128, min(8192, int(body.max_tokens)))

        for flag in ("internet_enabled", "image_enabled", "image_fallback", "voice_enabled", "voice_autojoin", "voice_listen"):
            value = getattr(body, flag)
            if value is not None:
                changes[flag] = bool(value)

        for key in ("web_search_model", "web_fetch_model", "image_model", "tts_model", "stt_model"):
            value = getattr(body, key)
            if value is not None:
                changes[key] = value.strip()[:200]

        if body.web_max_results is not None:
            changes["web_max_results"] = max(1, min(10, int(body.web_max_results)))

        if body.image_size is not None:
            size = body.image_size.strip().lower()
            if size not in IMAGE_SIZES:
                raise ApiError(400, f"Ukuran gambar tidak didukung: {size}.", code="invalid_image_size")
            changes["image_size"] = size

        voice_channel = current.get("voice_channel_id") or ""
        if body.voice_channel_id is not None:
            voice_channel = body.voice_channel_id.strip()
        if voice_channel and not voice_channel.isdigit():
            raise ApiError(400, "Voice Channel ID harus berupa angka.", code="invalid_voice_channel")
        changes["voice_channel_id"] = voice_channel

        if body.voice_language is not None:
            language = body.voice_language.strip().lower()
            if (language and not language.isalpha()) or len(language) > 8:
                raise ApiError(400, "Kode bahasa suara tidak valid.", code="invalid_language")
            changes["voice_language"] = language

        if body.image_token is not None:
            changes["image_token"] = body.image_token.strip()

        store.update_gateway(gateway_id, changes)
        try:
            await hub.sync_voice(gateway_id)
        except Exception as exc:
            # log not available on hub; ignore
            pass
        gateway = store.get_gateway(gateway_id)
        return {
            "settings": store.gateway_public(gateway),
            "status": hub.status(gateway_id),
        }

    @router.get("/gateways/{gateway_id}/cron")
    async def list_gateway_cron(gateway_id: str):
        store = get_store()
        gateway = store.get_gateway(gateway_id)
        if not gateway or gateway.get("id") != gateway_id:
            raise ApiError(404, "Gateway tidak ditemukan.", code="gateway_not_found")
        jobs = cronmod.sanitize_jobs(gateway.get("cron_jobs"))
        for j in jobs:
            try:
                j["next_run"] = cronmod.next_run(j["schedule"], cronmod.now()).timestamp()
            except cronmod.CronError:
                j["next_run"] = None
        return {"cron_jobs": jobs}

    @router.post("/gateways/{gateway_id}/cron")
    async def create_gateway_cron(gateway_id: str, body: CronJobUpdate):
        store = get_store()
        gateway = store.get_gateway(gateway_id)
        if not gateway or gateway.get("id") != gateway_id:
            raise ApiError(404, "Gateway tidak ditemukan.", code="gateway_not_found")
        schedule = (body.schedule or "").strip()
        prompt = (body.prompt or "").strip()
        channel_id = (body.channel_id or "").strip().strip("<#>")
        try:
            job = cronmod.make_job(schedule, prompt, channel_id)
        except cronmod.CronError as exc:
            raise ApiError(400, str(exc), code="invalid_cron") from exc
        jobs = cronmod.sanitize_jobs(gateway.get("cron_jobs"))
        if job["id"] in {j["id"] for j in jobs}:
            raise ApiError(409, "ID cron sudah ada.", code="cron_id_exists")
        store.update_gateway(gateway_id, {"cron_jobs": jobs + [job]})
        return {"settings": store.gateway_public(store.get_gateway(gateway_id)),
                "status": hub.status(gateway_id), "job": job}

    @router.put("/gateways/{gateway_id}/cron/{job_id}")
    async def update_gateway_cron(gateway_id: str, job_id: str, body: CronJobUpdate):
        store = get_store()
        gateway = store.get_gateway(gateway_id)
        if not gateway or gateway.get("id") != gateway_id:
            raise ApiError(404, "Gateway tidak ditemukan.", code="gateway_not_found")
        jobs = cronmod.sanitize_jobs(gateway.get("cron_jobs"))
        idx = next((i for i, j in enumerate(jobs) if str(j.get("id")) == job_id), None)
        if idx is None:
            raise ApiError(404, "Cron job tidak ditemukan.", code="cron_not_found")
        job = dict(jobs[idx])
        if body.enabled is not None:
            job["enabled"] = bool(body.enabled)
        if body.schedule is not None:
            try:
                job["schedule"] = cronmod.validate((body.schedule or "").strip())
            except cronmod.CronError as exc:
                raise ApiError(400, str(exc), code="invalid_cron") from exc
        if body.prompt is not None:
            job["prompt"] = (body.prompt or "").strip()[:4000]
        if body.channel_id is not None:
            cid = (body.channel_id or "").strip().strip("<#>")
            if not cid.isdigit():
                raise ApiError(400, "Channel ID harus berupa angka.", code="invalid_channel")
            job["channel_id"] = cid
        jobs[idx] = job
        store.update_gateway(gateway_id, {"cron_jobs": jobs})
        return {"settings": store.gateway_public(store.get_gateway(gateway_id)),
                "status": hub.status(gateway_id), "job": job}

    @router.delete("/gateways/{gateway_id}/cron/{job_id}")
    async def delete_gateway_cron(gateway_id: str, job_id: str):
        store = get_store()
        gateway = store.get_gateway(gateway_id)
        if not gateway or gateway.get("id") != gateway_id:
            raise ApiError(404, "Gateway tidak ditemukan.", code="gateway_not_found")
        jobs = cronmod.sanitize_jobs(gateway.get("cron_jobs"))
        kept = [j for j in jobs if str(j.get("id")) != job_id]
        if len(kept) == len(jobs):
            raise ApiError(404, "Cron job tidak ditemukan.", code="cron_not_found")
        store.update_gateway(gateway_id, {"cron_jobs": kept})
        return {"status": "ok"}

    @router.post("/gateways/{gateway_id}/cron/{job_id}/run")
    async def run_gateway_cron(gateway_id: str, job_id: str):
        manager = get_hub().get(gateway_id, create=False)
        if manager is None or not manager.status().get("ready"):
            raise ApiError(400, "Bot belum siap, cron tidak bisa dijalankan.", code="bot_not_ready")
        jobs = manager._cron_jobs()
        job = next((j for j in jobs if str(j.get("id")) == job_id), None)
        if job is None:
            raise ApiError(404, "Cron job tidak ditemukan.", code="cron_not_found")
        ok, detail = await manager._run_cron_job(job)
        return {"ok": ok, "detail": detail}

    @router.post("/gateways/{gateway_id}/start")
    async def start_gateway(gateway_id: str):
        try:
            status = await hub.start(gateway_id)
        except ValueError as exc:
            raise ApiError(400, str(exc), code="bot_not_ready") from exc
        except KeyError as exc:
            raise ApiError(404, str(exc), code="gateway_not_found") from exc
        except Exception as exc:
            raise ApiError(503, f"Bot gagal dijalankan: {exc}", code="bot_error") from exc
        return {"status": status}

    @router.post("/gateways/{gateway_id}/stop")
    async def stop_gateway(gateway_id: str):
        status = await hub.stop(gateway_id)
        return {"status": status}

    @router.post("/gateways/{gateway_id}/restart")
    async def restart_gateway(gateway_id: str):
        try:
            status = await hub.restart(gateway_id)
        except ValueError as exc:
            raise ApiError(400, str(exc), code="bot_not_ready") from exc
        except KeyError as exc:
            raise ApiError(404, str(exc), code="gateway_not_found") from exc
        except Exception as exc:
            raise ApiError(503, f"Bot gagal dijalankan ulang: {exc}", code="bot_error") from exc
        return {"status": status}

    @router.delete("/gateways/{gateway_id}")
    async def delete_gateway(gateway_id: str):
        store = get_store()
        gateway = store.get_gateway(gateway_id)
        if not gateway or gateway.get("id") != gateway_id:
            raise ApiError(404, "Gateway tidak ditemukan.", code="gateway_not_found")
        try:
            await hub.stop(gateway_id)
        except Exception:
            pass
        hub.remove(gateway_id)
        store.delete_gateway(gateway_id)
        return {"deleted": True}

    @router.get("/gateways/{gateway_id}/logs")
    async def gateway_logs(gateway_id: str):
        return {"logs": hub.logs(gateway_id)}

    @router.post("/gateways/{gateway_id}/logs/clear")
    async def clear_gateway_logs(gateway_id: str):
        hub.clear_logs(gateway_id)
        return {"logs": []}

    # ---------- backward-compatible aliases (first gateway) ----------

    @router.get("/gateway")
    async def get_gateway_compat():
        store = get_store()
        gateways = store.list_gateways()
        if not gateways:
            return {"settings": {}, "status": {}, "logs": []}
        gw = gateways[0]
        gid = gw.get("id") or ""
        return {
            "settings": store.gateway_public(gw),
            "status": hub.status(gid),
            "logs": hub.logs(gid),
        }

    @router.post("/gateway")
    async def save_gateway_compat(body: GatewayUpdate):
        store = get_store()
        gateways = store.list_gateways()
        if not gateways:
            raise ApiError(404, "Belum ada gateway.", code="gateway_not_found")
        gid = gateways[0].get("id") or ""
        # reuse update by calling endpoint function? duplicate minimal: delegate via direct call
        return await update_gateway(gid, body)

    @router.post("/gateway/start")
    async def start_gateway_compat():
        store = get_store()
        gateways = store.list_gateways()
        if not gateways:
            raise ApiError(404, "Belum ada gateway.", code="gateway_not_found")
        return await start_gateway(gateways[0].get("id") or "")

    @router.post("/gateway/stop")
    async def stop_gateway_compat():
        store = get_store()
        gateways = store.list_gateways()
        if not gateways:
            raise ApiError(404, "Belum ada gateway.", code="gateway_not_found")
        return await stop_gateway(gateways[0].get("id") or "")

    @router.post("/gateway/restart")
    async def restart_gateway_compat():
        store = get_store()
        gateways = store.list_gateways()
        if not gateways:
            raise ApiError(404, "Belum ada gateway.", code="gateway_not_found")
        return await restart_gateway(gateways[0].get("id") or "")

    @router.get("/gateway/logs")
    async def gateway_logs_compat():
        store = get_store()
        gateways = store.list_gateways()
        if not gateways:
            return {"logs": []}
        return {"logs": hub.logs(gateways[0].get("id") or "")}

    @router.post("/gateway/logs/clear")
    async def clear_gateway_logs_compat():
        store = get_store()
        gateways = store.list_gateways()
        if not gateways:
            return {"logs": []}
        hub.clear_logs(gateways[0].get("id") or "")
        return {"logs": []}

    return router
