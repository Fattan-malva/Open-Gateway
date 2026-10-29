import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from gateway.admin import create_router, require_local
from gateway.auth import ApiKeyStore
from gateway.browser import ChatResult
from gateway.engine import GatewayEngine
from gateway.errors import ApiError


class FakeAutomation:
    running = True
    headless = True

    async def new_chat(self, interactive_login=True):
        pass

    async def send_message(
        self,
        prompt,
        thinking=None,
        on_update=None,
        on_reasoning=None,
        interactive_login=True,
    ):
        if on_reasoning:
            on_reasoning("berpikir")
        if on_update:
            on_update("Halo dari DeepSeek")
        return ChatResult(
            text="Halo dari DeepSeek",
            reasoning="berpikir" if thinking else "",
        )

    async def is_logged_in(self):
        return True

    async def close(self):
        pass


def make_app(engine=None, local=True):
    app = FastAPI()
    app.include_router(create_router(engine or GatewayEngine(FakeAutomation())), prefix="/api")

    @app.exception_handler(ApiError)
    async def _handler(_request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    if local:
        app.dependency_overrides[require_local] = lambda: None
    return app


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ApiKeyStore(Path(self.tmp.name) / "api_keys.json")
        patcher = patch("gateway.admin.get_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(make_app())

    def test_rejects_non_local_clients(self):
        client = TestClient(make_app(local=False))
        response = client.get("/api/status")
        self.assertEqual(response.status_code, 403)

    def test_status_shape(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["app"], "open-masking")
        self.assertIn("deepseek-chat", data["models"])
        self.assertEqual(data["keys"], {"total": 0, "active": 0})

    def test_key_create_list_revoke(self):
        created = self.client.post("/api/keys", json={"name": "opencode"}).json()
        self.assertTrue(created["key"].startswith("sk-ds-"))
        self.assertNotIn("hash", created["record"])

        keys = self.client.get("/api/keys").json()["keys"]
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["name"], "opencode")
        self.assertFalse(keys[0]["revoked"])

        key_id = keys[0]["id"]
        response = self.client.post(f"/api/keys/{key_id}/revoke")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get("/api/keys").json()["keys"][0]["revoked"])

        missing = self.client.post("/api/keys/key_missing/revoke")
        self.assertEqual(missing.status_code, 404)

    def test_browser_status(self):
        data = self.client.get("/api/browser").json()
        self.assertEqual(data["state"], "ready")
        self.assertTrue(data["running"])
        self.assertTrue(data["logged_in"])
        self.assertIn("profile_dir", data)

    def test_connect_info(self):
        data = self.client.get("/api/connect").json()
        self.assertTrue(data["base_url"].endswith("/v1"))
        self.assertEqual(data["active_keys"], 0)

    def test_playground_stream(self):
        response = self.client.post(
            "/api/playground/chat",
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Halo"}],
                "thinking": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn("Halo dari DeepSeek", response.text)
        self.assertIn("[DONE]", response.text)

        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line[6:] != "[DONE]"
        ]
        self.assertEqual(events[-1]["type"], "done")

    def test_playground_rejects_empty_messages(self):
        response = self.client.post(
            "/api/playground/chat",
            json={"model": "deepseek-chat", "messages": []},
        )
        self.assertEqual(response.status_code, 400)

    def test_browser_reset_requires_confirmation(self):
        response = self.client.post("/api/browser/reset", json={"confirm": False})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
