import json
import tempfile
import unittest
from pathlib import Path

from gateway.store import ConfigStore, DEFAULT_SYSTEM_PROMPT, SCHEMA_VERSION


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        self.store = ConfigStore(self.path)

    def test_defaults_when_file_missing(self):
        data = self.store.get()
        self.assertEqual(data["version"], SCHEMA_VERSION)
        self.assertEqual(data["providers"], [])
        self.assertEqual(data["gateways"], [])
        self.assertFalse(self.path.exists())  # config tidak dibuat sampai user menyimpan

    def test_v1_migration(self):
        v1 = {
            "version": 1,
            "provider": {"base_url": "https://example.test/v1", "api_key": "k", "models": [], "catalog": {}, "updated_at": None},
            "gateway": {"discord_token": "tok", "model": "m1", "trigger": "mention", "prefix": "!ai", "max_history": 12, "max_tokens": 2048,
                        "internet_enabled": True, "image_enabled": True, "image_size": "1024x1024", "image_fallback": True,
                        "voice_enabled": False, "voice_language": "id", "system_prompt": DEFAULT_SYSTEM_PROMPT},
        }
        self.path.write_text(json.dumps(v1), encoding="utf-8")
        data = ConfigStore(self.path).get()
        self.assertEqual(data["version"], SCHEMA_VERSION)
        self.assertEqual(len(data["providers"]), 1)
        self.assertEqual(len(data["gateways"]), 1)
        self.assertEqual(data["providers"][0]["base_url"], "https://example.test/v1")
        self.assertEqual(data["gateways"][0]["discord_token"], "tok")
        self.assertEqual(data["gateways"][0]["provider_id"], data["providers"][0]["id"])

    def test_add_and_update_provider(self):
        provider = self.store.add_provider("nine_router", name="P1", base_url="https://example.test/v1", api_key="secret")
        self.assertEqual(provider["name"], "P1")
        pid = provider["id"]
        updated = self.store.update_provider(pid, {"api_key": "new-key", "base_url": "https://example.test/v2"})
        self.assertEqual(updated["api_key"], "new-key")
        self.assertEqual(updated["base_url"], "https://example.test/v2")
        reloaded = ConfigStore(self.path).get_provider(pid)
        self.assertEqual(reloaded["api_key"], "new-key")

    def test_set_catalog(self):
        provider = self.store.add_provider("openai", base_url="https://example.test/v1", api_key="k")
        catalog = {"chat": [{"id": "chat-1"}], "image": [{"id": "img-1"}]}
        self.store.set_catalog(provider["id"], catalog, 1700000001)
        stored = self.store.get_provider(provider["id"])
        self.assertEqual(stored["models"], [{"id": "chat-1"}])
        public = ConfigStore.provider_public(stored)
        self.assertEqual(public["catalog_counts"]["image"], 1)

    def test_gateway_crud(self):
        gw = self.store.add_gateway("discord", name="G1", provider_id="")
        gid = gw["id"]
        self.store.update_gateway(gid, {"discord_token": "tok", "model": "m1", "max_history": 20})
        stored = self.store.get_gateway(gid)
        self.assertEqual(stored["discord_token"], "tok")
        self.assertEqual(stored["max_history"], 20)
        public = ConfigStore.gateway_public(stored)
        self.assertTrue(public["discord_token_set"])
        self.assertNotIn("discord_token", public)
        self.assertTrue(self.store.delete_gateway(gid))
        self.assertEqual(self.store.get_gateway(gid), {})

    def test_broken_file_falls_back_to_defaults(self):
        self.path.write_text("{bukan json", encoding="utf-8")
        data = ConfigStore(self.path).get()
        self.assertEqual(data["gateways"], [])

    def test_provider_public_masks_key(self):
        provider = self.store.add_provider("openai", base_url="https://example.test/v1", api_key="secret-key")
        public = ConfigStore.provider_public(self.store.get_provider(provider["id"]))
        self.assertEqual(public["api_key"], "")
        self.assertTrue(public["api_key_set"])

    def test_whatsapp_gateway_disabled(self):
        gw = self.store.add_gateway("whatsapp", name="WA")
        self.assertFalse(gw["enabled"])


if __name__ == "__main__":
    unittest.main()
