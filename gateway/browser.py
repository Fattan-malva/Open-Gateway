"""Otomatisasi UI chat.deepseek.com via Playwright."""
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from playwright.async_api import async_playwright

from .config import (
    BASE_URL,
    BROWSER_CONFIG_KEYS,
    CHROME_CHANNEL,
    COOKIES_FILE,
    DEBUG,
    DEBUG_DIR,
    HEADLESS,
    LOGIN_TIMEOUT,
    PROFILE_DIR,
    REPLY_TIMEOUT,
)


def _selectors(env_name: str, defaults: tuple) -> tuple:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return defaults
    # Pisahkan dengan "||" agar selector yang mengandung koma tetap utuh.
    return tuple(part.strip() for part in raw.split("||") if part.strip())


CHAT_INPUT_SELECTORS = _selectors(
    "DS_INPUT_SELECTOR",
    ("#chat-input", "textarea[placeholder]", "textarea", "div[contenteditable='true']"),
)
SEND_BUTTON_SELECTORS = _selectors(
    "DS_SEND_SELECTOR",
    (
        "button[aria-label='Send message']",
        "button[aria-label*='send' i]",
        "button[aria-label*='kirim' i]",
        "button[type='submit']",
    ),
)
STOP_SELECTORS = _selectors(
    "DS_STOP_SELECTOR",
    (
        "button[aria-label*='stop' i]",
        "button[aria-label*='henti' i]",
        "button[aria-label*='berhenti' i]",
    ),
)
THINKING_SELECTORS = _selectors(
    "DS_THINKING_SELECTOR",
    (
        "div[class*='ds-toggle-button']:has-text('Deep thinking')",
        "[aria-pressed]:has-text('Deep thinking')",
        "div[class*='ds-toggle-button']:has-text('DeepThink')",
        "div[class*='ds-toggle-button']:has-text('深度思考')",
        "button:has-text('DeepThink')",
        "[role='button']:has-text('DeepThink')",
        "button:has-text('深度思考')",
        "[aria-label*='DeepThink' i]",
        "[aria-label*='thinking' i]",
        "button:has-text('R1')",
    ),
)
SEARCH_SELECTORS = _selectors(
    "DS_SEARCH_SELECTOR",
    (
        "button[aria-label*='search the web' i]",
        "button[aria-label*='web search' i]",
        "button[aria-label*='search' i]",
        "div[class*='ds-toggle-button']:has-text('Search')",
        "button:has-text('Search')",
        "[role='button']:has-text('Search')",
        "button:has-text('联网搜索')",
        "[role='button']:has-text('联网搜索')",
        "button:has-text('搜索')",
        "[role='button']:has-text('搜索')",
    ),
)
REASONING_SELECTORS = _selectors(
    "DS_REASONING_SELECTOR",
    (".ds-think-content", "[class*='ds-think-content']"),
)
REPLY_SELECTOR = ".ds-markdown"

logger = logging.getLogger("open_masking.browser")


@dataclass
class ChatResult:
    text: str
    reasoning: str = ""


def _cookie_entries_from_env() -> List[dict]:
    entries: List[dict] = []

    def add(name, value, domain=".deepseek.com", path="/"):
        if name and value:
            entries.append(
                {"name": str(name).strip(), "value": str(value).strip(), "domain": domain, "path": path}
            )

    raw = os.getenv("DS_COOKIE", "").strip()
    if raw:
        data = None
        if raw[:1] in ("[", "{"):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None

        if isinstance(data, dict) and "name" in data and "value" in data:
            data = [data]

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("name"):
                    add(
                        item["name"],
                        item.get("value", ""),
                        item.get("domain") or ".deepseek.com",
                        item.get("path") or "/",
                    )
        elif isinstance(data, dict):
            for name, value in data.items():
                add(name, value)
        else:
            for pair in raw.split(";"):
                if "=" in pair:
                    name, value = pair.split("=", 1)
                    add(name, value)

    for key, value in os.environ.items():
        if key.startswith("DS_") and key not in BROWSER_CONFIG_KEYS and value.strip():
            add(key[3:], value)

    return entries


class DeepSeekBrowserAutomation:
    def __init__(self):
        self.context = None
        self.page = None
        self.playwright = None
        self.headless = HEADLESS
        self._thinking_applied: Optional[bool] = None
        self._thinking_warned = False
        self._search_applied: Optional[bool] = None
        self._search_warned = False

    @property
    def running(self) -> bool:
        try:
            return self.page is not None and not self.page.is_closed()
        except Exception:
            return False

    async def is_logged_in(self) -> bool:
        return await self._is_logged_in()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self, headless: Optional[bool] = None):
        self.headless = HEADLESS if headless is None else headless
        self.playwright = await async_playwright().start()
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        channel = None if CHROME_CHANNEL.lower() in ("", "chromium", "default") else CHROME_CHANNEL
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel=channel,
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
                ignore_default_args=["--enable-automation"],
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=TranslateUI",
                    "--disable-notifications",
                ],
            )
        except Exception as exc:
            await self.playwright.stop()
            self.playwright = None
            raise RuntimeError(
                f"Gagal membuka Chrome ({CHROME_CHANNEL}): {exc}. "
                "Pastikan Chrome terpasang dan tidak ada jendela lain yang memakai profil ini, "
                "atau set DS_CHANNEL=chromium lalu install Chromium Playwright."
            ) from exc

        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def login(self, interactive: bool = True):
        if not self.page:
            raise RuntimeError("Browser belum dimulai")

        cookies = self._load_cookies()
        if cookies:
            await self.context.add_cookies(cookies)

        await self.page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(2500)
        self._thinking_applied = None
        self._search_applied = None

        if await self._is_logged_in():
            logger.info("Sesi DeepSeek aktif.")
        elif interactive:
            if self.headless:
                raise RuntimeError(
                    "Login manual membutuhkan jendela Chrome. Buka tab Browser di web "
                    "open-masking dan klik Login (server akan membuka jendela terlihat)."
                )
            logger.info("Belum login ke DeepSeek; menunggu login manual di jendela Chrome.")
            deadline = time.monotonic() + LOGIN_TIMEOUT
            while time.monotonic() < deadline:
                await asyncio.sleep(1)
                if await self._is_logged_in():
                    break
            if not await self._is_logged_in():
                raise RuntimeError(
                    "Login DeepSeek belum selesai. Buka tab Browser di web open-masking, "
                    "klik Login, lalu selesaikan login di jendela Chrome."
                )
            logger.info("Login DeepSeek berhasil.")
        else:
            raise RuntimeError(
                "Belum login ke DeepSeek. Buka tab Browser di web open-masking dan klik Login."
            )

        await self.save_cookies()

    def _load_cookies(self) -> List[dict]:
        entries: List[dict] = []

        if COOKIES_FILE.exists():
            try:
                data = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("name"):
                            entries.append(
                                {
                                    "name": item["name"],
                                    "value": str(item.get("value", "")),
                                    "domain": item.get("domain") or ".deepseek.com",
                                    "path": item.get("path") or "/",
                                }
                            )
            except (json.JSONDecodeError, OSError):
                pass

        entries.extend(_cookie_entries_from_env())

        merged = {}
        for cookie in entries:
            merged[(cookie["name"], cookie["domain"])] = cookie
        return list(merged.values())

    async def save_cookies(self):
        if not self.context:
            return
        try:
            cookies = await self.context.cookies()
            cookies = [c for c in cookies if "deepseek.com" in (c.get("domain") or "")]
            if not cookies:
                return
            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            COOKIES_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        except Exception:
            pass

    async def _is_logged_in(self) -> bool:
        if not self.page or "/sign_in" in self.page.url:
            return False
        return await self._find_input() is not None

    async def _find_input(self):
        for selector in CHAT_INPUT_SELECTORS:
            locator = self.page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    return locator
            except Exception:
                continue
        return None

    async def _ensure_chat_page(self, interactive_login: bool = True):
        if not self.page:
            raise RuntimeError("Browser belum dimulai")
        if await self._is_logged_in():
            return
        await self.login(interactive=interactive_login)
        if not await self._find_input():
            raise RuntimeError("Kotak chat DeepSeek tidak ditemukan setelah login.")

    async def new_chat(self, interactive_login: bool = True):
        await self._ensure_chat_page(interactive_login)
        await self.page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(1500)
        self._search_applied = None
        if not await self._find_input():
            await self._ensure_chat_page(interactive_login)

    async def _find_thinking_toggle(self):
        for selector in THINKING_SELECTORS:
            locator = self.page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    return locator
            except Exception:
                continue
        return None

    async def _toggle_state(self, locator) -> Optional[bool]:
        for attr in ("aria-pressed", "aria-checked", "data-state", "data-selected"):
            try:
                value = await locator.get_attribute(attr)
            except Exception:
                value = None
            if value is not None:
                value = value.strip().lower()
                if value in ("true", "on", "checked", "1", "active", "selected"):
                    return True
                if value in ("false", "off", "unchecked", "0", "inactive", "unselected"):
                    return False
        try:
            classes = (await locator.get_attribute("class") or "").lower()
        except Exception:
            classes = ""
        if re.search(r"\b(active|selected|checked|enabled)\b", classes):
            return True
        return None

    async def set_thinking(self, enabled: bool):
        if self._thinking_applied is enabled:
            return
        toggle = await self._find_thinking_toggle()
        if not toggle:
            if enabled:
                if DEBUG and not self._thinking_warned:
                    self._thinking_warned = True
                    logger.debug("Toggle DeepThink tidak ditemukan; atur DS_THINKING_SELECTOR bila perlu.")
                raise RuntimeError(
                    "Mode Deep Think tidak ditemukan pada UI DeepSeek. "
                    "Periksa tombol Deep Think atau atur DS_THINKING_SELECTOR."
                )
            self._thinking_applied = False
            return

        state = await self._toggle_state(toggle)
        should_click = state != enabled if state is not None else (enabled or self._thinking_applied is True)
        if should_click:
            try:
                await toggle.click()
                await asyncio.sleep(0.3)
            except Exception as exc:
                if DEBUG:
                    logger.debug("Gagal mengubah DeepThink: %s", exc)
                return
        self._thinking_applied = enabled

    async def _find_search_toggle(self):
        for selector in SEARCH_SELECTORS:
            locator = self.page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    return locator
            except Exception:
                continue
        return None

    async def set_search(self, enabled: bool):
        if self._search_applied is enabled:
            return
        toggle = await self._find_search_toggle()
        if not toggle:
            if enabled:
                raise RuntimeError(
                    "Mode Search tidak ditemukan pada UI DeepSeek. "
                    "Periksa tombol Search atau atur DS_SEARCH_SELECTOR."
                )
            self._search_applied = False
            return

        state = await self._toggle_state(toggle)
        should_click = state != enabled if state is not None else (enabled or self._search_applied is True)
        if should_click:
            try:
                await toggle.click()
                await asyncio.sleep(0.3)
            except Exception as exc:
                if DEBUG:
                    logger.debug("Gagal mengubah mode Search: %s", exc)
                if enabled:
                    raise RuntimeError(f"Gagal mengaktifkan mode Search: {exc}") from exc
                return
        self._search_applied = enabled

    async def send_message(
        self,
        message: str,
        timeout: Optional[int] = None,
        thinking: Optional[bool] = None,
        search: Optional[bool] = None,
        on_update: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        interactive_login: bool = True,
    ) -> ChatResult:
        timeout = timeout or REPLY_TIMEOUT
        await self._ensure_chat_page(interactive_login)

        if thinking is not None:
            await self.set_thinking(thinking)
        if search is not None:
            await self.set_search(search)

        input_box = await self._find_input()
        if not input_box:
            raise RuntimeError("Kotak chat DeepSeek tidak ditemukan.")

        before = await self.page.locator(REPLY_SELECTOR).count()
        reasoning_before = await self._reasoning_count()

        try:
            await input_box.click()
            await input_box.fill(message)
            await input_box.press("Enter")
            # Tunggu sampai composer kosong atau tombol Stop muncul.
            # Pada beberapa versi UI, Enter hanya membuat baris baru; gunakan
            # tombol Send sebagai fallback tanpa mengirim prompt dua kali.
            for _ in range(12):
                await asyncio.sleep(0.1)
                if not (await self._input_text(input_box)).strip() or await self._is_generating():
                    break

            if (await self._input_text(input_box)).strip() and not await self._is_generating():
                await self._click_send()

            return await self._wait_for_reply(
                before, timeout, on_update, on_reasoning, reasoning_before
            )
        except Exception:
            await self.save_debug("send_error")
            raise

    async def _input_text(self, input_box) -> str:
        try:
            return await input_box.input_value()
        except Exception:
            try:
                return await input_box.inner_text()
            except Exception:
                return ""

    async def _reply_text(self, reply) -> str:
        """Ambil teks balasan sebagai markdown, termasuk pagar blok kode.

        UI DeepSeek menampilkan toolbar "Copy/Download" di dalam blok kode;
        inner_text biasa akan membuang pagar ``` dan mencampur label toolbar.
        """
        js = """
        (element) => {
            const clone = element.cloneNode(true);
            clone.querySelectorAll('button,[role="button"]').forEach((el) => el.remove());

            const codeBlocks = Array.from(
                clone.querySelectorAll('.md-code-block, [class*="code-block"]')
            );
            for (const block of codeBlocks) {
                const pre = block.querySelector('pre');
                let lang = '';
                const banner = block.querySelector('[class*="banner"], [class*="header"]');
                if (banner) {
                    const labels = Array.from(banner.querySelectorAll('span'))
                        .map((el) => (el.textContent || '').trim())
                        .filter((t) => t && !/^(copy|download|salin|unduh)$/i.test(t));
                    if (labels.length) lang = labels[0].split(/\\s+/)[0];
                }
                if (!lang && pre) {
                    const code = pre.querySelector('code');
                    const match = code && (code.className || '').match(/language-([\\w-]+)/);
                    if (match) lang = match[1];
                }

                let replacement;
                if (pre) {
                    const codeText = (pre.textContent || '').replace(/\\n$/, '');
                    replacement = '```' + lang + '\\n' + codeText + '\\n```';
                } else {
                    // Blok kode kadang muncul sebelum <pre> selesai dirender.
                    replacement = '```' + lang + '\\n';
                }
                block.replaceWith(document.createTextNode(replacement));
            }

            clone.querySelectorAll('pre').forEach((pre) => {
                if (pre.closest('.md-code-block, [class*="code-block"]')) return;
                const codeText = (pre.textContent || '').replace(/\\n$/, '');
                pre.replaceWith(document.createTextNode('```\\n' + codeText + '\\n```'));
            });

            const host = document.createElement('div');
            host.style.cssText = 'position:fixed;left:-99999px;top:0;white-space:pre-wrap;';
            host.appendChild(clone);
            document.body.appendChild(host);
            const out = host.innerText || '';
            host.remove();
            return out;
        }
        """
        try:
            return await reply.evaluate(js)
        except Exception:
            try:
                return await reply.inner_text()
            except Exception:
                return ""

    async def _click_send(self):
        for selector in SEND_BUTTON_SELECTORS:
            button = self.page.locator(selector).first
            try:
                if await button.count() and await button.is_visible() and await button.is_enabled():
                    await button.click()
                    return
            except Exception:
                continue
        await self.page.keyboard.press("Enter")

    async def _is_generating(self) -> bool:
        for selector in STOP_SELECTORS:
            button = self.page.locator(selector).first
            try:
                if await button.count() and await button.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _reasoning_locator(self):
        return self.page.locator(", ".join(REASONING_SELECTORS))

    async def _reasoning_count(self) -> int:
        try:
            return await (await self._reasoning_locator()).count()
        except Exception:
            return 0

    async def _latest_reasoning(self, before_count: int) -> str:
        try:
            locator = await self._reasoning_locator()
            count = await locator.count()
        except Exception:
            return ""
        if count <= before_count:
            return ""

        best = ""
        for index in range(before_count, count):
            element = locator.nth(index)
            try:
                tag = (await element.evaluate("el => el.tagName")).lower()
                if tag in ("button", "svg", "path"):
                    continue
                if await element.evaluate("el => !!el.closest('button')"):
                    continue
                if await element.evaluate("el => !!el.closest('.ds-markdown')"):
                    continue
                # text_content tetap terbaca walau panel reasoning sedang collapsed.
                text = ((await element.text_content()) or "").strip()
                if len(text) > len(best):
                    best = text
            except Exception:
                continue
        return best

    async def _wait_for_reply(
        self,
        before_count: int,
        timeout_seconds: int,
        on_update: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        reasoning_before: int = 0,
    ) -> ChatResult:
        deadline = time.monotonic() + timeout_seconds
        last_text = ""
        last_reasoning = ""
        stable_since = None

        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)

            if "/sign_in" in self.page.url:
                raise RuntimeError(
                    "Sesi DeepSeek berakhir. Buka tab Browser di web open-masking lalu login ulang."
                )

            reasoning = await self._latest_reasoning(reasoning_before)
            if reasoning and reasoning != last_reasoning:
                if on_reasoning and reasoning.startswith(last_reasoning):
                    delta = reasoning[len(last_reasoning) :]
                    if delta:
                        on_reasoning(delta)
                last_reasoning = reasoning

            count = await self.page.locator(REPLY_SELECTOR).count()
            if count <= before_count:
                continue

            reply = self.page.locator(REPLY_SELECTOR).nth(count - 1)
            text = (await self._reply_text(reply)).strip()
            if last_reasoning and text.startswith(last_reasoning):
                text = text[len(last_reasoning) :].lstrip()
            if not text:
                continue

            if text != last_text:
                if on_update and text.startswith(last_text):
                    delta = text[len(last_text) :]
                    if delta:
                        on_update(delta)
                last_text = text
                stable_since = time.monotonic()
                continue

            if stable_since and time.monotonic() - stable_since >= 2.5 and not await self._is_generating():
                reasoning = last_reasoning or await self._latest_reasoning(reasoning_before)
                return ChatResult(text=last_text, reasoning=reasoning)

        if last_text:
            suffix = "\n\n[...balasan terpotong karena timeout...]"
            if on_update:
                on_update(suffix)
            reasoning = last_reasoning or await self._latest_reasoning(reasoning_before)
            return ChatResult(text=last_text + suffix, reasoning=reasoning)
        raise TimeoutError(f"DeepSeek tidak mengirim balasan dalam {timeout_seconds} detik.")

    async def save_debug(self, tag: str):
        if not DEBUG or not self.page:
            return
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            base = DEBUG_DIR / f"{tag}-{stamp}"
            await self.page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
            base.with_suffix(".html").write_text(await self.page.content(), encoding="utf-8")
            logger.debug("Debug tersimpan: %s.png / .html", base)
        except Exception:
            pass

    async def close(self):
        if self.context:
            try:
                await self.save_cookies()
            except Exception:
                pass
        try:
            if self.context:
                await self.context.close()
        finally:
            self.context = None
            self.page = None
            self._thinking_applied = None
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
