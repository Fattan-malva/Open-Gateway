"""Akses internet untuk bot: pencarian web dan ekstraksi halaman."""
import base64
import html
import ipaddress
import re
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote, urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_TEXT = 12000
PRIVATE_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost"}


class WebError(Exception):
    pass


def _strip_noise(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|template|svg)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    return raw


def html_to_text(raw: str, limit: int = MAX_TEXT) -> str:
    """Ubah HTML menjadi teks polos yang rapi."""
    text = _strip_noise(raw or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article|blockquote)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned = []
    blank = False
    for line in lines:
        if not line:
            if not blank and cleaned:
                cleaned.append("")
            blank = True
            continue
        blank = False
        cleaned.append(line)
    result = "\n".join(cleaned).strip()
    if len(result) > limit:
        result = result[:limit].rstrip() + "…"
    return result


def page_title(raw: str, fallback: str = "") -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw or "")
    if match:
        return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip() or fallback
    return fallback


def normalize_url(raw: str) -> str:
    """Ambil URL http/https yang aman dari sebuah teks."""
    value = (raw or "").strip().strip("<>()[]\"'")
    if not value:
        raise WebError("URL kosong.")
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise WebError("Hanya URL http/https yang didukung.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise WebError("URL tidak valid.")
    if host in PRIVATE_HOSTS or host.endswith(".local"):
        raise WebError("URL lokal tidak diizinkan.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        addresses = set()
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise WebError("URL lokal tidak diizinkan.")
    return value


def find_urls(text: str) -> List[str]:
    raw = re.findall(r"https?://[^\s<>\"')\]]+", text or "")
    urls = []
    for item in raw:
        cleaned = item.rstrip(".,;:!?")
        if cleaned not in urls:
            urls.append(cleaned)
    return urls[:3]


# ------------------------------------------------------------------- search


def _ddg_target(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.hostname or ""):
        params = parse_qs(parsed.query)
        target = (params.get("uddg") or [""])[0]
        if target:
            return target
    return href


def _bing_target(href: str) -> str:
    href = html.unescape(href)
    match = re.search(r"[?&]u=a1([A-Za-z0-9_\-]+)", href)
    if match:
        encoded = match.group(1)
        padding = "=" * (-len(encoded) % 4)
        try:
            return base64.urlsafe_b64decode(encoded + padding).decode("utf-8", "replace")
        except (ValueError, TypeError):
            return href
    return href


def parse_duckduckgo_lite(raw: str, max_results: int = 5) -> List[Dict[str, str]]:
    results = []
    links = re.findall(
        r"(?is)<a[^>]+class=[\"']result-link[\"'][^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        raw or "",
    )
    snippets = re.findall(
        r"(?is)<td[^>]+class=[\"']result-snippet[\"'][^>]*>(.*?)</td>", raw or ""
    )
    for index, (href, title) in enumerate(links[:max_results]):
        snippet = snippets[index] if index < len(snippets) else ""
        results.append(
            {
                "title": html_to_text(title, 300),
                "url": _ddg_target(href),
                "snippet": html_to_text(snippet, 400),
            }
        )
    return [item for item in results if item["url"].startswith("http")]


def parse_bing(raw: str, max_results: int = 5) -> List[Dict[str, str]]:
    results = []
    blocks = re.split(r'(?i)<li class="b_algo"', raw or "")[1:]
    for block in blocks[: max_results * 2]:
        link = re.search(r'(?is)<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block)
        if not link:
            continue
        snippet = re.search(r'(?is)<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', block)
        if not snippet:
            snippet = re.search(r"(?is)<p[^>]*>(.*?)</p>", block)
        results.append(
            {
                "title": html_to_text(link.group(2), 300),
                "url": _bing_target(link.group(1)),
                "snippet": html_to_text(snippet.group(1) if snippet else "", 400),
            }
        )
        if len(results) >= max_results:
            break
    return [item for item in results if item["url"].startswith("http")]


async def search_web(query: str, max_results: int = 5, timeout: float = 20) -> Dict[str, Any]:
    """Pencarian web tanpa API key (DuckDuckGo Lite, lalu Bing)."""
    query = (query or "").strip()
    if not query:
        raise WebError("Query pencarian kosong.")
    max_results = max(1, min(10, int(max_results or 5)))
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "id-ID,id;q=0.9,en;q=0.8"}
    errors = []

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get("https://lite.duckduckgo.com/lite/", params={"q": query})
            if response.status_code < 400:
                results = parse_duckduckgo_lite(response.text, max_results)
                if results:
                    return {"provider": "duckduckgo", "query": query, "results": results}
            errors.append(f"duckduckgo {response.status_code}")
    except httpx.HTTPError as exc:
        errors.append(f"duckduckgo: {exc}")

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get("https://www.bing.com/search", params={"q": query, "setlang": "id"})
            if response.status_code < 400:
                results = parse_bing(response.text, max_results)
                if results:
                    return {"provider": "bing", "query": query, "results": results}
            errors.append(f"bing {response.status_code}")
    except httpx.HTTPError as exc:
        errors.append(f"bing: {exc}")

    raise WebError("Pencarian gagal: " + "; ".join(errors or ["tidak ada hasil"]))


# -------------------------------------------------------------------- fetch


async def fetch_page(url: str, max_chars: int = 8000, timeout: float = 25) -> Dict[str, Any]:
    """Ambil isi halaman sebagai markdown/teks. Coba Jina Reader lalu unduh langsung."""
    target = normalize_url(url)
    max_chars = max(500, min(20000, int(max_chars or 8000)))
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "id-ID,id;q=0.9,en;q=0.8"}
    errors = []

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get("https://r.jina.ai/" + target)
        if response.status_code < 400 and len(response.text.strip()) > 80:
            text = response.text
            title_match = re.search(r"(?m)^Title:\s*(.+)$", text)
            title = title_match.group(1).strip() if title_match else target
            if "Markdown Content:" in text:
                text = text.split("Markdown Content:", 1)[1]
            text = text.strip()[:max_chars]
            return {"provider": "jina", "url": target, "title": title, "text": text}
        errors.append(f"jina {response.status_code}")
    except httpx.HTTPError as exc:
        errors.append(f"jina: {exc}")

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(target)
        if response.status_code < 400:
            text = html_to_text(response.text, max_chars)
            if len(text.strip()) > 40:
                return {
                    "provider": "direct",
                    "url": str(response.url),
                    "title": page_title(response.text, target),
                    "text": text,
                }
        errors.append(f"direct {response.status_code}")
    except httpx.HTTPError as exc:
        errors.append(f"direct: {exc}")

    raise WebError("Gagal mengambil halaman: " + "; ".join(errors or ["tidak diketahui"]))


def format_search_results(payload: Dict[str, Any], max_chars: int = 4000) -> str:
    lines = []
    for index, item in enumerate(payload.get("results") or [], start=1):
        title = (item.get("title") or "").strip() or "(tanpa judul)"
        url = (item.get("url") or "").strip()
        snippet = re.sub(r"\s+", " ", (item.get("snippet") or "")).strip()
        lines.append(f"{index}. {title}\n   {url}\n   {snippet}")
    text = "\n".join(lines)
    if payload.get("answer"):
        text = f"Ringkasan: {payload['answer']}\n\n{text}"
    return text[:max_chars] or "Tidak ada hasil."


def quote_prompt(prompt: str) -> str:
    return quote(prompt or "", safe="")
