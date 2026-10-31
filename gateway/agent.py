"""Agen percakapan: model bisa mencari di web, membuka halaman, dan membuat gambar."""
import json
from typing import Any, Dict, List, Optional

from . import media, webtools
import provider

MAX_ROUNDS = 4
MAX_IMAGES = 2
MAX_TOOL_OUTPUT = 6000

TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "web_search": {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Cari informasi terbaru di internet. Gunakan saat pengguna bertanya tentang berita, "
                "harga, jadwal, kejadian terkini, atau hal yang mungkin berubah setelah data latihanmu."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Kata kunci pencarian."}
                },
                "required": ["query"],
            },
        },
    },
    "web_fetch": {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Buka satu URL dan ambil isi halamannya sebagai teks. Gunakan untuk membaca artikel atau dokumentasi.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL http/https yang ingin dibuka."}
                },
                "required": ["url"],
            },
        },
    },
    "generate_image": {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Buat gambar dari deskripsi teks. Gunakan saat pengguna meminta gambar, ilustrasi, "
                "poster, atau visual. Tulis prompt yang detail dalam bahasa Inggris."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Deskripsi gambar yang diinginkan."}
                },
                "required": ["prompt"],
            },
        },
    },
}


def build_tools(settings: Dict[str, Any], allow_image: bool = True) -> List[Dict[str, Any]]:
    tools = []
    if settings.get("internet_enabled", True):
        tools.append(TOOL_SCHEMAS["web_search"])
        tools.append(TOOL_SCHEMAS["web_fetch"])
    if allow_image and settings.get("image_enabled", True):
        tools.append(TOOL_SCHEMAS["generate_image"])
    return tools


def _tool_args(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class ToolRunner:
    def __init__(self, provider_cfg: Dict[str, Any], settings: Dict[str, Any], log=None):
        self.provider_cfg = provider_cfg or {}
        self.settings = settings or {}
        self.log = log
        self.images: List[Dict[str, Any]] = []
        self.used: List[str] = []

    def _warn(self, message: str) -> None:
        if self.log:
            self.log("warn", message)

    async def execute(self, name: str, raw_args: Any) -> str:
        args = _tool_args(raw_args)
        if name not in TOOL_SCHEMAS:
            return f"Tool tidak dikenal: {name}"

        if name == "web_search":
            query = str(args.get("query") or "").strip()
            if not query:
                return "Query pencarian kosong."
            self.used.append("web_search")
            return await self._search(query)

        if name == "web_fetch":
            url = str(args.get("url") or "").strip()
            if not url:
                return "URL kosong."
            self.used.append("web_fetch")
            return await self._fetch(url)

        if name == "generate_image":
            prompt = str(args.get("prompt") or args.get("text") or "").strip()
            if not prompt:
                return "Prompt gambar kosong."
            self.used.append("generate_image")
            return await self._image(prompt)

        return "Tool tidak dijalankan."

    async def _search(self, query: str) -> str:
        max_results = int(self.settings.get("web_max_results") or 5)
        model = (self.settings.get("web_search_model") or "").strip()
        if model:
            try:
                payload = await provider.web_search(
                    self.provider_cfg,
                    model,
                    query,
                    max_results,
                )
                if payload.get("results"):
                    return webtools.format_search_results(payload)
            except provider.ProviderError as exc:
                self._warn(f"Pencarian 9Router gagal: {exc.message[:160]}")
        try:
            payload = await webtools.search_web(query, max_results)
            return webtools.format_search_results(payload)
        except webtools.WebError as exc:
            return f"Pencarian gagal: {exc}"

    async def _fetch(self, url: str) -> str:
        model = (self.settings.get("web_fetch_model") or "").strip()
        if model:
            try:
                payload = await provider.web_fetch(
                    self.provider_cfg,
                    model,
                    url,
                )
                if payload.get("text"):
                    text = payload["text"][:MAX_TOOL_OUTPUT]
                    return f"Judul: {payload.get('title') or url}\nURL: {payload.get('url') or url}\n\n{text}"
            except provider.ProviderError as exc:
                self._warn(f"Fetch 9Router gagal: {exc.message[:160]}")
        try:
            page = await webtools.fetch_page(url, MAX_TOOL_OUTPUT)
        except webtools.WebError as exc:
            return f"Gagal membuka halaman: {exc}"
        return f"Judul: {page['title']}\nURL: {page['url']}\n\n{page['text']}"

    async def _image(self, prompt: str) -> str:
        if len(self.images) >= MAX_IMAGES:
            return "Batas jumlah gambar per pesan sudah tercapai."
        try:
            image = await media.generate_image(
                self.provider_cfg, self.settings, prompt, log=self.log
            )
        except provider.ProviderError as exc:
            return f"Gambar gagal dibuat: {exc.message}"
        image["prompt"] = prompt
        self.images.append(image)
        return (
            "Gambar berhasil dibuat dan akan dilampirkan otomatis oleh sistem. "
            "Jangan menulis URL atau markdown gambar; cukup jelaskan singkat hasilnya."
        )


async def run(
    provider_cfg: Dict[str, Any],
    settings: Dict[str, Any],
    messages: List[Dict[str, Any]],
    max_tokens: Optional[int] = None,
    log=None,
) -> Dict[str, Any]:
    """Jalankan percakapan dengan dukungan tool, kembalikan teks + gambar."""
    model = (settings.get("model") or "").strip()
    runner = ToolRunner(provider_cfg, settings, log=log)
    tools = build_tools(settings)
    working = list(messages)

    for _round in range(MAX_ROUNDS):
        active_tools = tools if tools else None
        try:
            if active_tools and len(runner.images) >= MAX_IMAGES:
                active_tools = [t for t in tools if t["function"]["name"] != "generate_image"] or None
            reply = await provider.chat_message(
                provider_cfg, model, working, max_tokens, tools=active_tools
            )
        except provider.ProviderError as exc:
            if active_tools and exc.status_code == 400:
                tools = []
                reply = await provider.chat_message(
                    provider_cfg, model, working, max_tokens, tools=None
                )
            else:
                raise

        calls = reply["tool_calls"]
        if not calls:
            text = reply["text"] or "_(model tidak mengembalikan teks jawaban)_"
            return {"text": text, "images": runner.images, "used": runner.used}

        working.append(
            {
                "role": "assistant",
                "content": reply["text"] or "",
                "tool_calls": calls,
            }
        )
        for call in calls:
            function = call.get("function") or {}
            result = await runner.execute(function.get("name") or "", function.get("arguments"))
            working.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": str(result)[:MAX_TOOL_OUTPUT],
                }
            )

    final = await provider.chat_message(provider_cfg, model, working, max_tokens, tools=None)
    text = final["text"] or "_(model tidak mengembalikan teks jawaban)_"
    return {"text": text, "images": runner.images, "used": runner.used}


async def prefetch(text: str, settings: Dict[str, Any], log=None) -> str:
    """Ambil isi URL pertama di pesan pengguna sebagai konteks tambahan."""
    if not settings.get("internet_enabled", True):
        return ""
    urls = webtools.find_urls(text)
    if not urls:
        return ""
    try:
        page = await webtools.fetch_page(urls[0], 5000)
    except webtools.WebError as exc:
        if log:
            log("warn", f"Gagal membuka tautan: {exc}")
        return ""
    if log:
        log("info", f"Membaca tautan: {urls[0]}")
    return (
        f"\n\n--- Isi halaman {page['url']} ---\n"
        f"{page['title']}\n{page['text'][:5000]}\n--- akhir halaman ---"
    )
