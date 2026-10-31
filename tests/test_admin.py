import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from gateway.admin import create_router, require_local
from gateway.errors import ApiError
from provider.client import ProviderError
from gateway.store import ConfigStore


class AdminTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ConfigStore(Path(self.tmp.name) / "config.json")
        app = FastAPI()
        app.include_router(create_router(), prefix="/api")
        app.dependency_overrides[require_local] = lambda: None

        @app.exception_handler(ApiError)
        async def _handle(_request: Request, exc: ApiError):
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        self.client = TestClient(app)
        self.store_patch = patch("gateway.admin.get_store", return_value=self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        # hub & bot punya import get_store sendiri: ikut di-patch agar test tidak baca data/config.json asli
        for target in ("gateway.discord.hub.get_store", "gateway.discord.bot.get_store"):
            p = patch(target, return_value=self.store)
            p.start()
            self.addCleanup(p.stop)

    def test_status(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("version", data)
        self.assertIn("paths", data)

    def test_provider_types(self):
        response = self.client.get("/api/provider/types")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["types"]), 2)

    def test_create_and_get_provider(self):
        response = self.client.post("/api/providers", json={"type": "openai", "base_url": "https://example.test/v1", "api_key": "key-123", "fetch": False})
        self.assertEqual(response.status_code, 200)
        pid = response.json()["provider"]["id"]
        response = self.client.get(f"/api/providers/{pid}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["base_url"], "https://example.test/v1")

    def test_save_provider_fetches_models(self):
        async def fake_fetch(cfg):
            self.assertEqual(cfg["base_url"], "https://example.test/v1")
            return {"catalog": {"chat": [{"id": "m1", "owned_by": "test"}], "image": [{"id": "img-1"}]}, "errors": {"web": "tidak tersedia"}}

        with patch("gateway.admin.providers.fetch_catalog", fake_fetch):
            response = self.client.post("/api/providers", json={"type": "openai", "base_url": "https://example.test/v1/", "api_key": "key-123", "fetch": True})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data["warning"])
        self.assertEqual(data["provider"]["model_count"], 1)
        self.assertEqual(data["catalog_errors"], {"web": "tidak tersedia"})

    def test_save_provider_reports_warning_when_models_fail(self):
        async def fake_fetch(_cfg):
            raise ProviderError("provider down")

        with patch("gateway.admin.providers.fetch_catalog", fake_fetch):
            response = self.client.post("/api/providers", json={"type": "openai", "base_url": "https://example.test/v1", "api_key": "key-123", "fetch": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["warning"], "provider down")

    def test_provider_refresh(self):
        provider = self.store.add_provider("openai", base_url="https://example.test/v1", api_key="key")
        async def fake_fetch(_cfg):
            return {"catalog": {"chat": [{"id": "m2"}]}, "errors": {}}
        with patch("gateway.admin.providers.fetch_catalog", fake_fetch):
            response = self.client.post(f"/api/providers/{provider['id']}/refresh")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider"]["models"][0]["id"], "m2")

    def test_gateway_types(self):
        response = self.client.get("/api/gateway/types")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json()["types"]), 2)

    def test_create_gateway_and_update(self):
        provider = self.store.add_provider("openai", base_url="https://example.test/v1", api_key="k")
        response = self.client.post("/api/gateways", json={"type": "discord", "provider_id": provider["id"]})
        self.assertEqual(response.status_code, 200)
        gid = response.json()["settings"]["id"]
        response = self.client.post(f"/api/gateways/{gid}", json={"model": "m1", "trigger": "mention", "max_history": 999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settings"]["max_history"], 50)

    def test_gateway_coming_soon(self):
        response = self.client.post("/api/gateways", json={"type": "whatsapp"})
        self.assertEqual(response.status_code, 400)

    def test_start_and_stop(self):
        provider = self.store.add_provider("openai", base_url="https://example.test/v1", api_key="k")
        response = self.client.post("/api/gateways", json={"type": "discord", "provider_id": provider["id"]})
        gid = response.json()["settings"]["id"]
        self.store.update_gateway(gid, {"discord_token": "token-1234", "model": "m1"})
        response = self.client.post(f"/api/gateways/{gid}/start")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(f"/api/gateways/{gid}/stop")
        self.assertEqual(response.status_code, 200)

    def test_logs_and_clear(self):
        provider = self.store.add_provider("openai", base_url="https://example.test/v1", api_key="k")
        response = self.client.post("/api/gateways", json={"type": "discord", "provider_id": provider["id"]})
        gid = response.json()["settings"]["id"]
        response = self.client.get(f"/api/gateways/{gid}/logs")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(f"/api/gateways/{gid}/logs/clear")
        self.assertEqual(response.status_code, 200)

    def test_compat_gateway_endpoint(self):
        provider = self.store.add_provider("openai", base_url="https://example.test/v1", api_key="k")
        response = self.client.post("/api/gateways", json={"type": "discord", "provider_id": provider["id"]})
        gid = response.json()["settings"]["id"]
        self.store.update_gateway(gid, {"discord_token": "tok", "model": "m1"})
        response = self.client.get("/api/gateway")
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/gateway", json={"model": "m2"})
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
