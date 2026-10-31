"""Menghubungkan model AI dari provider ke bot Discord (chat, internet, gambar, suara)."""
import asyncio
import io
import re
import time
from collections import deque
from typing import Any, Dict, List, Optional

import discord

from .. import agent, media, webtools
import provider as providers
from ..store import get_store
from .voice import VoiceManager

MAX_CHUNKS = 4
CHUNK_LIMIT = 1900
MAX_UPLOAD = 8 * 1024 * 1024
COMMANDS = {
    "help",
    "bantuan",
    "reset",
    "ping",
    "model",
    "join",
    "leave",
    "dengar",
    "gambar",
    "cari",
    "buka",
    "voice",
}


def command_name(text: str) -> str:
    """Ambil nama perintah dari teks, mis. '!ai reset' -> 'reset'."""
    value = (text or "").strip().lower()
    return value.lstrip("!/ ").strip().split(" ", 1)[0]


def is_command(text: str) -> bool:
    return command_name(text) in COMMANDS


def command_args(text: str) -> str:
    """Ambil sisa teks setelah nama perintah."""
    value = (text or "").strip().lstrip("!/ ").strip()
    parts = value.split(" ", 1)
    return parts[1].strip() if len(parts) > 1 else ""


def strip_prefix(text: str, prefix: str) -> str:
    """Hapus prefix di awal pesan tanpa membedakan huruf besar/kecil."""
    value = (text or "").strip()
    prefix = (prefix or "").strip()
    if not prefix:
        return value
    lowered = value.lower()
    if lowered == prefix.lower():
        return ""
    if lowered.startswith(prefix.lower() + " "):
        return value[len(prefix):].strip()
    return value


def split_message(text: str, limit: int = CHUNK_LIMIT) -> List[str]:
    text = (text or "").strip()
    if not text:
        return ["_(kosong)_"]
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n ")
    if text:
        chunks.append(text)
    if len(chunks) > MAX_CHUNKS:
        chunks = chunks[:MAX_CHUNKS]
        chunks[-1] += "\n\n_…pesan dipotong._"
    return chunks


def _help_text(prefix: str) -> str:
    return (
        "**Open Gateway — bantuan**\n"
        "• Mention bot atau kirim DM untuk mengobrol dengan model AI.\n"
        "• AI bisa mencari di internet, membuka tautan, dan membuat gambar sendiri.\n"
        f"• `{prefix} gambar <deskripsi>` — buat gambar langsung.\n"
        f"• `{prefix} cari <kata kunci>` — cari di internet.\n"
        f"• `{prefix} buka <url>` — baca isi sebuah halaman.\n"
        f"• `{prefix} join [id channel]` — ajak bot masuk voice channel.\n"
        f"• `{prefix} leave` — keluar dari voice channel.\n"
        f"• `{prefix} dengar on|off` — atur bot mendengarkan suara.\n"
        f"• `{prefix} voice` — status suara bot.\n"
        f"• `{prefix} reset` — hapus riwayat percakapan di channel ini.\n"
        f"• `{prefix} model` — tampilkan model yang sedang aktif.\n"
        f"• `{prefix} ping` — cek latensi bot.\n"
        f"• `{prefix} help` — tampilkan pesan ini."
    )


class DiscordBotManager:
    def __init__(self, gateway_id: str = ""):
        self.gateway_id = gateway_id
        self._client: Optional[discord.Client] = None
        self._task: Optional[asyncio.Task] = None
        self._state = "stopped"
        self._last_error: Optional[str] = None
        self._started_at: Optional[int] = None
        self._ready_at: Optional[int] = None
        self._handled = 0
        self._history: Dict[str, List[Dict[str, str]]] = {}
        self._logs: deque = deque(maxlen=150)
        self._lock = asyncio.Lock()
        self.voice = VoiceManager()
        self.voice.configure(self.log, self._settings, self._provider)
        self.voice.on_transcript = self._handle_voice_transcript

    # ---------- logging & status ----------

    def _settings(self) -> Dict[str, Any]:
        store = get_store()
        gateway = store.get_gateway(self.gateway_id) if self.gateway_id else store.get_gateway()
        return gateway or {}

    def _provider(self) -> Dict[str, Any]:
        store = get_store()
        settings = self._settings()
        provider_id = settings.get("provider_id") or ""
        provider = store.get_provider(provider_id) if provider_id else store.get_provider()
        return provider or {}

    def log(self, level: str, message: str) -> None:
        self._logs.appendleft(
            {"time": int(time.time()), "level": level, "message": str(message)[:500]}
        )

    def logs(self) -> List[Dict[str, Any]]:
        return list(self._logs)

    def clear_logs(self) -> None:
        self._logs.clear()

    def status(self) -> Dict[str, Any]:
        client = self._client
        ready = bool(client is not None and not client.is_closed() and client.is_ready())
        settings = self._settings()
        return {
            "state": self._state,
            "ready": ready,
            "running": bool(self._task is not None and not self._task.done()),
            "user": str(client.user) if client is not None and client.user else None,
            "user_id": client.user.id if client is not None and client.user else None,
            "guilds": len(client.guilds) if client is not None else 0,
            "latency_ms": round(client.latency * 1000) if ready else None,
            "started_at": self._started_at,
            "ready_at": self._ready_at,
            "last_error": self._last_error,
            "handled": self._handled,
            "model": settings.get("model") or "",
            "trigger": settings.get("trigger") or "mention",
            "internet": bool(settings.get("internet_enabled", True)),
            "image": bool(settings.get("image_enabled", True)),
            "voice": self.voice.status(),
        }

    # ---------- lifecycle ----------

    async def autostart(self) -> None:
        settings = self._settings()
        if not (settings.get("discord_token") or "").strip():
            return
        try:
            await self.start()
        except Exception as exc:
            self._state = "error"
            self._last_error = str(exc)
            self.log("error", f"Autostart gagal: {exc}")

    async def start(self) -> Dict[str, Any]:
        async with self._lock:
            if self._task is not None and not self._task.done():
                return self.status()

            settings = self._settings()
            token = (settings.get("discord_token") or "").strip()
            if not token:
                raise ValueError("Discord bot token belum diisi. Simpan token terlebih dahulu.")
            if not (settings.get("model") or "").strip():
                raise ValueError("Model AI belum dipilih. Pilih model di tab Gateway.")

            self._state = "starting"
            self._last_error = None
            self._started_at = int(time.time())
            self._ready_at = None
            self._client = self._build_client()
            self._task = asyncio.create_task(self._run(self._client, token), name="open-gateway-discord")
            self.log("info", "Menghubungkan bot ke Discord...")
            return self.status()

    async def _run(self, client: discord.Client, token: str) -> None:
        try:
            await client.start(token)
        except asyncio.CancelledError:
            raise
        except discord.LoginFailure:
            self._state = "error"
            self._last_error = "Token Discord tidak valid (LoginFailure)."
            self.log("error", self._last_error)
        except discord.PrivilegedIntentsRequired:
            self._state = "error"
            self._last_error = "Message Content Intent belum diaktifkan di Discord Developer Portal."
            self.log("error", self._last_error)
        except Exception as exc:
            self._state = "error"
            self._last_error = str(exc)
            self.log("error", f"Bot berhenti: {exc}")
        finally:
            self._ready_at = None
            try:
                await self.voice.leave()
            except Exception:
                pass
            if self._state != "error":
                self._state = "stopped"
            try:
                if not client.is_closed():
                    await client.close()
            except Exception:
                pass

    async def stop(self) -> Dict[str, Any]:
        async with self._lock:
            client = self._client
            task = self._task
            if client is None and task is None:
                self._state = "stopped"
                return self.status()

            self._state = "stopping"
            self.log("info", "Menghentikan bot Discord...")
            try:
                await self.voice.leave()
            except Exception:
                pass
            if client is not None and not client.is_closed():
                try:
                    await client.close()
                except Exception:
                    pass
            if task is not None and not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=10)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    task.cancel()
                except Exception:
                    pass
            self._client = None
            self._task = None
            self._state = "stopped"
            self._ready_at = None
            self.log("info", "Bot Discord berhenti.")
            return self.status()

    async def restart(self) -> Dict[str, Any]:
        await self.stop()
        return await self.start()

    async def shutdown(self) -> None:
        try:
            await self.stop()
        except Exception:
            pass

    async def sync_voice(self) -> None:
        """Sesuaikan voice dengan pengaturan terbaru (tanpa restart bot)."""
        client = self._client
        if client is None or client.is_closed() or not client.is_ready():
            return
        settings = self._settings()
        if not settings.get("voice_enabled"):
            await self.voice.leave()
            return
        channel_id = (settings.get("voice_channel_id") or "").strip()
        if settings.get("voice_autojoin") and channel_id.isdigit():
            if self.voice.channel_id != int(channel_id):
                try:
                    await self.voice.join_by_id(int(channel_id))
                except Exception as exc:
                    self.voice.last_error = str(exc)
                    self.log("warn", f"Gagal bergabung ke voice: {exc}")
        if self.voice.voice_client is not None and self.voice.status()["connected"]:
            self.voice.set_listening(bool(settings.get("voice_listen", True)))

    # ---------- discord client ----------

    def _build_client(self) -> discord.Client:
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self.voice.attach(client)

        @client.event
        async def on_ready() -> None:
            manager = self
            manager._state = "running"
            manager._ready_at = int(time.time())
            manager._last_error = None
            manager.log(
                "ok",
                f"Bot online sebagai {client.user} di {len(client.guilds)} server.",
            )
            try:
                await manager.sync_voice()
            except Exception as exc:
                manager.log("warn", f"Gagal menyiapkan voice: {exc}")

        @client.event
        async def on_disconnect() -> None:
            if not client.is_closed():
                self.log("warn", "Koneksi Discord terputus. Mencoba menyambung ulang...")

        @client.event
        async def on_error(event: str, *_args: Any) -> None:
            self.log("error", f"Error Discord pada event {event}.")

        @client.event
        async def on_message(message: discord.Message) -> None:
            try:
                await self._handle_message(client, message)
            except Exception as exc:
                self.log("error", f"Gagal memproses pesan: {exc}")

        return client

    # ---------- message handling ----------

    def _should_reply(self, client: discord.Client, message: discord.Message, settings: Dict[str, Any]) -> bool:
        content = (message.content or "").strip()
        if client.user is None:
            return False
        if message.guild is None:
            return True
        if client.user in message.mentions:
            return True

        prefix = (settings.get("prefix") or "!ai").strip()
        without_prefix = strip_prefix(content, prefix)
        if without_prefix != content and is_command(without_prefix):
            return True

        trigger = settings.get("trigger") or "mention"
        if trigger == "all":
            channel_id = str(settings.get("channel_id") or "").strip()
            return bool(channel_id) and str(message.channel.id) == channel_id
        if trigger == "prefix":
            return strip_prefix(content, prefix) != content
        return False

    def _clean_text(self, client: discord.Client, message: discord.Message, settings: Dict[str, Any]) -> str:
        text = message.content or ""
        if client.user is not None:
            text = re.sub(rf"<@!?{client.user.id}>", " ", text)
        text = strip_prefix(text, (settings.get("prefix") or "!ai").strip())
        return text.strip() or "Halo"

    async def _handle_message(self, client: discord.Client, message: discord.Message) -> None:
        if message.author.bot or client.user is None:
            return
        settings = self._settings()
        if not self._should_reply(client, message, settings):
            return

        text = self._clean_text(client, message, settings)
        prefix = (settings.get("prefix") or "!ai").strip()
        command = command_name(text)
        args = command_args(text)
        author_name = getattr(message.author, "display_name", str(message.author))

        if command in ("help", "bantuan"):
            await self._reply(message, _help_text(prefix))
            return
        if command == "reset":
            self._history.pop(str(message.channel.id), None)
            await self._reply(message, "Riwayat percakapan di channel ini sudah dihapus.")
            self.log("info", f"Riwayat dihapus oleh {message.author}.")
            return
        if command == "ping":
            latency = round(client.latency * 1000) if client.latency else 0
            await self._reply(message, f"Pong! Latensi {latency} ms.")
            return
        if command == "model":
            model = settings.get("model") or "(belum dipilih)"
            extras = []
            if settings.get("internet_enabled"):
                extras.append("internet aktif")
            if settings.get("image_enabled"):
                extras.append("gambar aktif")
            if self.voice.status()["connected"]:
                extras.append(f"voice #{self.voice.channel_name}")
            suffix = f"\nFitur: {', '.join(extras)}" if extras else ""
            await self._reply(message, f"Model aktif: `{model}`{suffix}")
            return
        if command == "join":
            await self._command_join(message, settings, args)
            return
        if command == "leave":
            await self.voice.leave()
            await self._reply(message, "Keluar dari voice channel.")
            return
        if command == "dengar":
            await self._command_listen(message, args)
            return
        if command == "voice":
            status = self.voice.status()
            lines = [
                f"Voice: {'tersambung ke #' + str(status['channel']) if status['connected'] else 'tidak tersambung'}",
                f"Mendengarkan: {'ya' if status['listening'] else 'tidak'}",
                f"Penerima suara tersedia: {'ya' if status['recv_available'] else 'tidak'}",
            ]
            if status.get("last_error"):
                lines.append(f"Error terakhir: {status['last_error'][:300]}")
            await self._reply(message, "\n".join(lines))
            return
        if command == "gambar":
            await self._command_image(message, settings, args)
            return
        if command == "cari":
            await self._command_search(message, args)
            return
        if command == "buka":
            await self._command_fetch(message, args)
            return

        model = (settings.get("model") or "").strip()
        if not model:
            await self._reply(message, "Model AI belum dipilih. Atur di tab Gateway pada web UI.")
            return

        started = time.time()
        self.log(
            "info",
            f"{message.author} di #{getattr(message.channel, 'name', 'DM')} → {model}",
        )
        try:
            async with message.channel.typing():
                result = await self._ask(settings, str(message.channel.id), text, author_name)
        except providers.ProviderError as exc:
            self.log("error", f"Provider gagal: {exc.message}")
            await self._reply(message, f"⚠️ Gagal menghubungi model: {exc.message[:1500]}")
            return
        except Exception as exc:
            self.log("error", f"Error tak terduga: {exc}")
            await self._reply(message, f"⚠️ Terjadi kesalahan: {str(exc)[:1500]}")
            return

        self._handled += 1
        answer = result["text"]
        elapsed = time.time() - started
        self.log(
            "ok",
            f"Balasan terkirim ({elapsed:.1f}s, {len(answer)} karakter, "
            f"{len(result.get('images') or [])} gambar).",
        )
        await self._reply(message, answer, result.get("images"))
        if result.get("used"):
            self.log("info", f"Tool dipakai: {', '.join(dict.fromkeys(result['used']))}")
        if self.voice.status()["connected"]:
            asyncio.create_task(self.voice.say(answer))

    # ---------- commands ----------

    async def _command_join(self, message: discord.Message, settings: Dict[str, Any], args: str) -> None:
        target = args.strip().strip("<#>") or (settings.get("voice_channel_id") or "").strip()
        author_channel = getattr(getattr(message.author, "voice", None), "channel", None)
        try:
            if target.isdigit():
                await self.voice.join_by_id(int(target), message.channel)
            elif author_channel is not None:
                await self.voice.join(author_channel, message.channel)
            else:
                await self._reply(
                    message,
                    "Masukkan Voice Channel ID (`!ai join 123456789`) atau masuk ke voice channel dulu.",
                )
                return
        except Exception as exc:
            self.voice.last_error = str(exc)
            await self._reply(message, f"Gagal bergabung ke voice: {str(exc)[:400]}")
            return
        await self._reply(message, f"Bergabung ke voice #{self.voice.channel_name}. Sekarang saya bisa ikut ngobrol.")

    async def _command_listen(self, message: discord.Message, args: str) -> None:
        value = args.strip().lower()
        status = self.voice.status()
        if not status["connected"]:
            await self._reply(message, "Bot belum masuk voice channel. Gunakan `join` dulu.")
            return
        enabled = status["listening"]
        if value in ("on", "ya", "1", "true"):
            enabled = True
        elif value in ("off", "tidak", "0", "false"):
            enabled = False
        else:
            enabled = not enabled
        listening = self.voice.set_listening(enabled)
        await self._reply(message, f"Mendengarkan suara: {'aktif' if listening else 'nonaktif'}.")

    async def _command_image(self, message: discord.Message, settings: Dict[str, Any], prompt: str) -> None:
        if not prompt:
            await self._reply(message, "Tulis deskripsi gambar, mis. `!ai gambar kucing astronot di bulan`.")
            return
        if not settings.get("image_enabled"):
            await self._reply(message, "Fitur gambar sedang dimatikan di web UI.")
            return
        try:
            async with message.channel.typing():
                image = await media.generate_image(
                    self._provider(), settings, prompt, log=self.log
                )
        except providers.ProviderError as exc:
            await self._reply(message, f"⚠️ Gambar gagal dibuat: {exc.message[:800]}")
            return
        self._handled += 1
        await self._reply(message, f"Gambar untuk: {prompt[:300]}", [image])

    async def _command_search(self, message: discord.Message, query: str) -> None:
        if not query:
            await self._reply(message, "Tulis kata kunci, mis. `!ai cari harga bitcoin`.")
            return
        try:
            async with message.channel.typing():
                payload = await webtools.search_web(query, 5)
        except webtools.WebError as exc:
            await self._reply(message, f"⚠️ Pencarian gagal: {exc}")
            return
        await self._reply(message, webtools.format_search_results(payload))

    async def _command_fetch(self, message: discord.Message, url: str) -> None:
        if not url:
            await self._reply(message, "Tulis URL, mis. `!ai buka https://example.com`.")
            return
        try:
            async with message.channel.typing():
                page = await webtools.fetch_page(url, 4000)
        except webtools.WebError as exc:
            await self._reply(message, f"⚠️ Gagal membuka halaman: {exc}")
            return
        await self._reply(message, f"**{page['title']}**\n{page['url']}\n\n{page['text']}")

    # ---------- pipeline ----------

    async def _ask(
        self,
        settings: Dict[str, Any],
        history_key: str,
        text: str,
        author_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Jalankan percakapan lengkap (riwayat + tool) untuk satu pesan pengguna."""
        model = (settings.get("model") or "").strip()
        if not model:
            raise providers.ProviderError("Model AI belum dipilih. Atur di tab Gateway pada web UI.")

        history = self._history.setdefault(history_key, [])
        user_text = f"{author_name}: {text}" if author_name else text
        history.append({"role": "user", "content": user_text[:6000]})

        max_history = max(2, int(settings.get("max_history") or 12))
        if len(history) > max_history:
            del history[:-max_history]

        system_prompt = (settings.get("system_prompt") or "").strip()
        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(dict(item) for item in history)

        extra = await agent.prefetch(text, settings, log=self.log)
        if extra:
            messages[-1]["content"] = str(messages[-1]["content"]) + extra

        provider_cfg = self._provider()
        try:
            result = await agent.run(
                provider_cfg,
                settings,
                messages,
                max_tokens=int(settings.get("max_tokens") or 2048),
                log=self.log,
            )
        except Exception:
            history.pop()
            if not history:
                self._history.pop(history_key, None)
            raise

        answer = result["text"]
        history.append({"role": "assistant", "content": answer[:6000]})
        if len(history) > max_history:
            del history[:-max_history]
        return result

    async def _handle_voice_transcript(self, user: Any, text: str) -> None:
        settings = self._settings()
        if not settings.get("voice_enabled") or not (settings.get("model") or "").strip():
            return
        guild = getattr(self.voice.voice_client, "guild", None)
        channel = self.voice._text_channel  # type: ignore[attr-defined]
        if channel is None and guild is not None:
            channel = guild.system_channel
            if channel is None:
                for candidate in guild.text_channels:
                    try:
                        if candidate.permissions_for(guild.me).send_messages:
                            channel = candidate
                            break
                    except Exception:
                        continue
            self.voice._text_channel = channel  # type: ignore[attr-defined]

        key = f"voice:{self.voice.channel_id or 'dm'}"
        name = getattr(user, "display_name", str(user))
        try:
            result = await self._ask(settings, key, text, name)
        except providers.ProviderError as exc:
            self.log("warn", f"Model gagal menjawab suara: {exc.message[:300]}")
            return
        except Exception as exc:
            self.log("warn", f"Gagal memproses suara: {exc}")
            return

        self._handled += 1
        answer = result["text"]
        if channel is not None:
            try:
                await self._send_channel(channel, answer, result.get("images"))
            except Exception as exc:
                self.log("warn", f"Gagal mengirim teks jawaban suara: {exc}")
        await self.voice.say(answer)

    # ---------- sending ----------

    @staticmethod
    def _files(images: Optional[List[Dict[str, Any]]]) -> List[discord.File]:
        files = []
        for index, image in enumerate(images or [], start=1):
            data = image.get("data") or b""
            if not data or len(data) > MAX_UPLOAD:
                continue
            extension = media.extension_for(image.get("mime") or "image/png", "png")
            files.append(
                discord.File(
                    io.BytesIO(data),
                    filename=f"gambar-{index}.{extension}",
                    description=(image.get("prompt") or "")[:200],
                )
            )
        return files

    async def _reply(
        self,
        message: discord.Message,
        text: str,
        images: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        chunks = split_message(text)
        files = self._files(images)
        for index, chunk in enumerate(chunks):
            attachments = files if index == 0 else []
            try:
                if index == 0 and message.guild is not None:
                    await message.reply(
                        chunk,
                        files=attachments or discord.utils.MISSING,
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    await message.channel.send(
                        chunk,
                        files=attachments or discord.utils.MISSING,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except discord.HTTPException as exc:
                self.log("error", f"Gagal mengirim pesan Discord: {exc}")
                return

    async def _send_channel(self, channel: Any, text: str, images: Optional[List[Dict[str, Any]]] = None) -> None:
        chunks = split_message(text)
        files = self._files(images)
        for index, chunk in enumerate(chunks):
            attachments = files if index == 0 else []
            await channel.send(
                chunk,
                files=attachments or discord.utils.MISSING,
                allowed_mentions=discord.AllowedMentions.none(),
            )


_manager: Optional[DiscordBotManager] = None


def get_bot() -> DiscordBotManager:
    global _manager
    if _manager is None:
        _manager = DiscordBotManager()
    return _manager
