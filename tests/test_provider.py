import unittest
from unittest.mock import patch

import provider as providers
from provider import client as provider_module
from provider.client import (
    ProviderError,
    chat,
    chat_message,
    fetch_models,
    generate_image,
    normalize_models,
    speech,
    transcribe,
)
from provider.nine_router import client as nine_router_module


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", content=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = content if content is not None else (text or "").encode("utf-8")
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("bukan json")
        return self._payload


class FakeAsyncClient:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, headers=None, **kwargs):
        self.requests.append(("GET", url, None, headers))
        return self.response

    async def post(self, url, headers=None, json=None, **kwargs):
        self.requests.append(("POST", url, json or kwargs, headers))
        return self.response


def fake_client(response):
    client = FakeAsyncClient(response)

    def factory(**_kwargs):
        return client

    return client, factory


class NormalizeModelsTests(unittest.TestCase):
    def test_reads_capabilities_and_defaults(self):
        models = normalize_models([
            {"id": "deepseek/chat", "owned_by": "deepseek", "capabilities": {"vision": True, "tools": True, "reasoning": True, "contextWindow": 128000, "maxOutput": 8192}},
            {"id": "plain"},
            {"id": "  "},
            {"no_id": True},
        ])
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0]["id"], "deepseek/chat")
        self.assertTrue(models[0]["vision"])
        self.assertEqual(models[0]["context_length"], 128000)
        self.assertEqual(models[1]["id"], "plain")

    def test_deduplicates_and_sorts(self):
        models = normalize_models([{"id": "zeta"}, {"id": "Alpha"}, {"id": "zeta"}])
        self.assertEqual([m["id"] for m in models], ["Alpha", "zeta"])

    def test_ignores_non_dict_items(self):
        self.assertEqual(normalize_models([None, "x", 3]), [])


class FetchModelsTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_and_normalizes(self):
        response = FakeResponse(payload={"object": "list", "data": [{"id": "m1"}, {"id": "m2"}]})
        client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            models = await fetch_models("https://example.test/v1/", "key")
        self.assertEqual([m["id"] for m in models], ["m1", "m2"])
        method, url, _body, headers = client.requests[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "https://example.test/v1/models")
        self.assertEqual(headers["Authorization"], "Bearer key")

    async def test_missing_base_url(self):
        with self.assertRaises(ProviderError):
            await fetch_models("", "key")

    async def test_http_error_is_surfaced(self):
        response = FakeResponse(status_code=401, payload={"error": {"message": "API key salah"}})
        _client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            with self.assertRaises(ProviderError) as ctx:
                await fetch_models("https://example.test/v1", "bad")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("API key salah", ctx.exception.message)

    async def test_invalid_json(self):
        response = FakeResponse(payload=None, text="<html>oops</html>")
        _client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            with self.assertRaises(ProviderError):
                await fetch_models("https://example.test/v1", "key")

    async def test_non_list_payload(self):
        response = FakeResponse(payload={"data": "nope"})
        _client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            with self.assertRaises(ProviderError):
                await fetch_models("https://example.test/v1", "key")


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_message_content(self):
        response = FakeResponse(payload={"choices": [{"message": {"content": "  halo  "}}]})
        client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            answer = await chat("https://example.test/v1", "key", "m1", [{"role": "user", "content": "hai"}], max_tokens=64)
        self.assertEqual(answer, "halo")
        _method, url, body, headers = client.requests[0]
        self.assertEqual(url, "https://example.test/v1/chat/completions")
        self.assertEqual(body["model"], "m1")
        self.assertEqual(body["max_tokens"], 64)

    async def test_falls_back_to_reasoning_content(self):
        response = FakeResponse(payload={"choices": [{"message": {"reasoning_content": "pikir"}}]})
        _client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            answer = await chat("https://example.test/v1", "key", "m1", [])
        self.assertEqual(answer, "pikir")

    async def test_empty_answer_has_placeholder(self):
        response = FakeResponse(payload={"choices": [{"message": {"content": ""}}]})
        _client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            answer = await chat("https://example.test/v1", "key", "m1", [])
        self.assertIn("tidak mengembalikan teks", answer)

    async def test_requires_model(self):
        with self.assertRaises(ProviderError):
            await chat("https://example.test/v1", "key", "", [])

    async def test_provider_error_message(self):
        response = FakeResponse(status_code=429, payload={"error": {"message": "Free usage limit reached"}})
        _client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            with self.assertRaises(ProviderError) as ctx:
                await chat("https://example.test/v1", "key", "m1", [])
        self.assertEqual(ctx.exception.status_code, 429)


class ChatMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_tool_calls(self):
        response = FakeResponse(payload={"choices": [{"message": {"content": "Saya cari dulu.", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "bitcoin"}'}}]}}]})
        client, factory = fake_client(response)
        tools = [{"type": "function", "function": {"name": "web_search"}}]
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            result = await chat_message("https://example.test/v1", "key", "m1", [], tools=tools)
        self.assertEqual(result["text"], "Saya cari dulu.")
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "web_search")
        body = client.requests[0][2]
        self.assertEqual(body["tool_choice"], "auto")


class MediaEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_binary_response(self):
        response = FakeResponse(content=b"PNGDATA", headers={"content-type": "image/png"})
        client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            result = await generate_image("https://example.test/v1", "key", "img-model", "kucing", "512x512")
        self.assertEqual(result["data"], b"PNGDATA")
        self.assertEqual(result["mime"], "image/png")

    async def test_speech_returns_audio_bytes(self):
        response = FakeResponse(content=b"WAVDATA", headers={"content-type": "audio/wav"})
        _client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            result = await speech("https://example.test/v1", "key", "tts-model", "halo")
        self.assertEqual(result["data"], b"WAVDATA")
        self.assertEqual(result["mime"], "audio/wav")

    async def test_transcribe_reads_text(self):
        response = FakeResponse(payload={"text": " halo dunia "}, headers={})
        _client, factory = fake_client(response)
        with patch.object(provider_module.httpx, "AsyncClient", factory):
            text = await transcribe("https://example.test/v1", "key", "stt-model", "a.wav", b"WAV", "id")
        self.assertEqual(text, "halo dunia")


class ProviderRegistryTests(unittest.TestCase):
    def test_types_include_two(self):
        types = providers.provider_types_public()
        self.assertEqual(len(types), 2)
        self.assertEqual({t["id"] for t in types}, {"nine_router", "openai"})

    def test_nine_router_icon(self):
        meta = providers.provider_type("nine_router")
        self.assertEqual(meta["icon"], "assets/icon-9router.svg")


class NineRouterCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_gathers_all_kinds(self):
        async def fake_get(_base_url, _api_key, kind):
            return [{"id": f"{kind or 'chat'}-1"}]

        with patch.object(provider_module, "fetch_models", fake_get):
            result = await nine_router_module.fetch_catalog("https://example.test/v1", "key")
        self.assertEqual(result["errors"], {})
        self.assertEqual(result["catalog"]["chat"][0]["id"], "chat-1")
        self.assertEqual(result["catalog"]["image"][0]["id"], "image-1")

    async def test_optional_kind_failure_is_reported(self):
        async def fake_get(_base_url, _api_key, kind):
            if kind == "image":
                raise ProviderError("tidak ada model gambar")
            return [{"id": "m1"}]

        with patch.object(provider_module, "fetch_models", fake_get):
            result = await nine_router_module.fetch_catalog("https://example.test/v1", "key")
        self.assertEqual(result["catalog"]["image"], [])
        self.assertIn("tidak ada model gambar", result["errors"]["image"])

    async def test_chat_failure_is_fatal(self):
        async def fake_get(_base_url, _api_key, kind):
            if not kind:
                raise ProviderError("chat down")
            return []

        with patch.object(provider_module, "fetch_models", fake_get):
            with self.assertRaises(ProviderError):
                await nine_router_module.fetch_catalog("https://example.test/v1", "key")


if __name__ == "__main__":
    unittest.main()
