"""Manajer banyak instance bot Discord (per gateway)."""
import asyncio
from typing import Any, Dict, List, Optional

from ..store import get_store
from .bot import DiscordBotManager


class DiscordHub:
    def __init__(self) -> None:
        self._bots: Dict[str, DiscordBotManager] = {}

    def _configs(self) -> List[Dict[str, Any]]:
        return [g for g in get_store().list_gateways() if g.get("type") == "discord"]

    def get(self, gateway_id: str, create: bool = True) -> Optional[DiscordBotManager]:
        if gateway_id in self._bots:
            return self._bots[gateway_id]
        if not create:
            return None
        manager = DiscordBotManager(gateway_id)
        self._bots[gateway_id] = manager
        return manager

    def ensure_all(self) -> None:
        for config in self._configs():
            self.get(config.get("id") or "")

    def _stopped_status(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "state": "stopped",
            "ready": False,
            "running": False,
            "user": None,
            "user_id": None,
            "guilds": 0,
            "latency_ms": None,
            "started_at": None,
            "ready_at": None,
            "last_error": None,
            "handled": 0,
            "model": config.get("model") or "",
            "trigger": config.get("trigger") or "mention",
            "internet": bool(config.get("internet_enabled", True)),
            "image": bool(config.get("image_enabled", True)),
            "voice": {"connected": False, "channel": None, "listening": False},
            "gateway_id": config.get("id") or "",
            "name": config.get("name") or "",
        }

    async def autostart(self) -> None:
        self.ensure_all()
        for config in self._configs():
            if not config.get("enabled", True):
                continue
            token = (config.get("discord_token") or "").strip()
            if not token:
                continue
            model = (config.get("model") or "").strip()
            if not model:
                continue
            manager = self.get(config.get("id") or "")
            if manager is None:
                continue
            try:
                await manager.start()
            except Exception as exc:
                manager.log("error", f"Autostart gagal: {exc}")

    async def start(self, gateway_id: str) -> Dict[str, Any]:
        manager = self.get(gateway_id)
        if manager is None:
            raise KeyError("Gateway tidak ditemukan.")
        return await manager.start()

    async def stop(self, gateway_id: str) -> Dict[str, Any]:
        manager = self.get(gateway_id, create=False)
        if manager is None:
            config = get_store().get_gateway(gateway_id)
            return self._stopped_status(config)
        return await manager.stop()

    async def restart(self, gateway_id: str) -> Dict[str, Any]:
        manager = self.get(gateway_id)
        if manager is None:
            raise KeyError("Gateway tidak ditemukan.")
        return await manager.restart()

    async def shutdown(self) -> None:
        for gateway_id in list(self._bots):
            try:
                await self._bots[gateway_id].shutdown()
            except Exception:
                pass

    def status(self, gateway_id: Optional[str] = None) -> Any:
        if gateway_id:
            manager = self.get(gateway_id, create=False)
            if manager is not None:
                status = manager.status()
                status["gateway_id"] = gateway_id
                return status
            config = get_store().get_gateway(gateway_id)
            return self._stopped_status(config)
        result = []
        for config in self._configs():
            gid = config.get("id") or ""
            manager = self.get(gid, create=False)
            if manager is not None:
                status = manager.status()
            else:
                status = self._stopped_status(config)
            status["gateway_id"] = gid
            status["name"] = config.get("name") or ""
            result.append(status)
        return result

    def logs(self, gateway_id: str) -> List[Dict[str, Any]]:
        manager = self.get(gateway_id, create=False)
        if manager is None:
            return []
        return manager.logs()

    def clear_logs(self, gateway_id: str) -> None:
        manager = self.get(gateway_id, create=False)
        if manager is not None:
            manager.clear_logs()

    async def sync_voice(self, gateway_id: str) -> None:
        manager = self.get(gateway_id, create=False)
        if manager is not None:
            await manager.sync_voice()

    def remove(self, gateway_id: str) -> None:
        self._bots.pop(gateway_id, None)


_hub: Optional[DiscordHub] = None


def get_hub() -> DiscordHub:
    global _hub
    if _hub is None:
        _hub = DiscordHub()
    return _hub


def get_bot(gateway_id: str = "") -> DiscordBotManager:
    """Kompatibilitas lama: kembalikan manager pertama atau buat baru."""
    hub = get_hub()
    if gateway_id:
        manager = hub.get(gateway_id)
        if manager is not None:
            return manager
    configs = [g for g in get_store().list_gateways() if g.get("type") == "discord"]
    if configs:
        gid = configs[0].get("id") or ""
        manager = hub.get(gid)
        if manager is not None:
            return manager
    return DiscordBotManager(gateway_id)
