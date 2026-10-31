"""Suara Discord: bergabung ke voice channel, mendengarkan, dan berbicara."""
import asyncio
import io
import os
import shutil
import tempfile
import time
import wave
from array import array
from typing import Any, Awaitable, Callable, Dict, Optional

import discord

from .. import media
import provider

try:
    from discord.ext import voice_recv

    VOICE_RECV_AVAILABLE = True
except Exception:  # pragma: no cover - paket opsional
    voice_recv = None
    VOICE_RECV_AVAILABLE = False

SAMPLE_RATE = 48000
BYTES_PER_SECOND = SAMPLE_RATE * 2 * 2  # stereo, 16-bit
SILENCE_SECONDS = 1.2
MIN_SPEECH_SECONDS = 0.5
MAX_SPEECH_SECONDS = 20
MAX_SPOKEN_CHARS = 700


def pcm_to_wav(pcm: bytes) -> bytes:
    """Downmix PCM stereo 48 kHz 16-bit ke WAV mono."""
    usable = len(pcm) - (len(pcm) % 4)
    samples = array("h")
    samples.frombytes(pcm[:usable])
    mono = samples[0::2]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(mono.tobytes())
    return buffer.getvalue()


def duration_seconds(pcm: bytes) -> float:
    return len(pcm) / float(BYTES_PER_SECOND)


class _PcmSink(voice_recv.AudioSink if VOICE_RECV_AVAILABLE else object):  # type: ignore[misc]
    def __init__(self, manager: "VoiceManager"):
        if VOICE_RECV_AVAILABLE:
            super().__init__()
        self.manager = manager

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data) -> None:
        if user is None or getattr(user, "bot", False):
            return
        pcm = getattr(data, "pcm", None)
        if pcm:
            self.manager.feed(user, pcm)

    def cleanup(self) -> None:
        return None


class VoiceManager:
    """Mengelola satu voice client untuk bot Discord."""

    def __init__(self) -> None:
        self.client: Optional[discord.Client] = None
        self._voice: Optional[Any] = None
        self._text_channel: Optional[discord.abc.Messageable] = None
        self._buffers: Dict[int, Dict[str, Any]] = {}
        self._flusher: Optional[asyncio.Task] = None
        self._tasks: set = set()
        self._listening = False
        self._speak_lock = asyncio.Lock()
        self._log: Callable[[str, str], None] = lambda _level, _message: None
        self._settings_getter: Callable[[], Dict[str, Any]] = lambda: {}
        self._provider_getter: Callable[[], Dict[str, Any]] = lambda: {}
        self.on_transcript: Optional[Callable[[Any, str], Awaitable[None]]] = None
        self.last_error: Optional[str] = None
        self.channel_name: Optional[str] = None
        self.channel_id: Optional[int] = None

    # ------------------------------------------------------------ konfigurasi

    def configure(self, log, settings_getter, provider_getter) -> None:
        self._log = log
        self._settings_getter = settings_getter
        self._provider_getter = provider_getter

    def attach(self, client: discord.Client) -> None:
        self.client = client

    def status(self) -> Dict[str, Any]:
        voice = self._voice
        connected = bool(voice is not None and voice.is_connected())
        return {
            "connected": connected,
            "channel": self.channel_name if connected else None,
            "channel_id": self.channel_id if connected else None,
            "listening": bool(connected and self._listening),
            "recv_available": VOICE_RECV_AVAILABLE,
            "text_channel_id": getattr(self._text_channel, "id", None),
            "last_error": self.last_error,
        }

    @property
    def voice_client(self):
        return self._voice

    # ---------------------------------------------------------------- bergabung

    async def join(
        self,
        channel: Any,
        text_channel: Optional[discord.abc.Messageable] = None,
        listening: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if self.client is None:
            raise RuntimeError("Bot Discord belum siap.")
        if not isinstance(channel, discord.VoiceChannel):
            raise RuntimeError("Channel yang dipilih bukan voice channel.")
        if self._voice is not None and self._voice.is_connected():
            if self._voice.channel and self._voice.channel.id == channel.id:
                if text_channel is not None:
                    self._text_channel = text_channel
                self.last_error = None
                return self.status()
            await self.leave()

        settings = self._settings_getter()
        want_listen = settings.get("voice_listen", True) if listening is None else listening
        cls = None
        if want_listen and VOICE_RECV_AVAILABLE:
            cls = voice_recv.VoiceRecvClient

        try:
            if cls is not None:
                voice = await channel.connect(cls=cls, self_deaf=False, self_mute=False)
            else:
                voice = await channel.connect(self_deaf=True, self_mute=False)
        except TypeError:
            voice = await channel.connect(self_deaf=True, self_mute=False)
        except Exception as exc:
            self.last_error = str(exc)
            raise

        self._voice = voice
        self.channel_name = getattr(channel, "name", str(channel.id))
        self.channel_id = channel.id
        if text_channel is not None:
            self._text_channel = text_channel
        self.last_error = None
        self._log("ok", f"Bergabung ke voice #{self.channel_name}.")

        if want_listen and VOICE_RECV_AVAILABLE:
            try:
                voice.listen(_PcmSink(self))
                self._listening = True
                self._start_flusher()
                self._log("info", "Mendengarkan percakapan voice channel.")
            except Exception as exc:
                self._listening = False
                self._log("warn", f"Gagal mendengarkan voice: {exc}")
        return self.status()

    async def join_by_id(
        self,
        channel_id: int,
        text_channel: Optional[discord.abc.Messageable] = None,
    ) -> Dict[str, Any]:
        if self.client is None:
            raise RuntimeError("Bot Discord belum siap.")
        channel = self.client.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.client.fetch_channel(int(channel_id))
            except Exception as exc:
                raise RuntimeError(f"Voice channel {channel_id} tidak ditemukan.") from exc
        return await self.join(channel, text_channel)

    async def leave(self) -> None:
        self._stop_flusher()
        self._buffers.clear()
        voice = self._voice
        self._listening = False
        self.channel_name = None
        self.channel_id = None
        self._voice = None
        if voice is None:
            return
        try:
            if getattr(voice, "is_listening", lambda: False)():
                voice.stop_listening()
            if voice.is_playing():
                voice.stop()
            await voice.disconnect(force=True)
        except Exception:
            pass
        self._log("info", "Keluar dari voice channel.")

    async def reconnect(self) -> None:
        settings = self._settings_getter()
        channel_id = (settings.get("voice_channel_id") or "").strip()
        if not settings.get("voice_enabled") or not channel_id.isdigit():
            return
        try:
            await self.join_by_id(int(channel_id), self._text_channel)
        except Exception as exc:
            self.last_error = str(exc)
            self._log("warn", f"Gagal bergabung ke voice: {exc}")

    # ------------------------------------------------------------------ bicara

    async def say(self, text: str) -> bool:
        voice = self._voice
        if voice is None or not voice.is_connected():
            return False
        if not shutil.which("ffmpeg"):
            self._log("warn", "ffmpeg tidak ditemukan; suara tidak bisa diputar.")
            return False
        settings = self._settings_getter()
        if not (settings.get("tts_model") or "").strip():
            return False
        try:
            audio = await media.synthesize(self._provider_getter(), settings, text)
        except provider.ProviderError as exc:
            self._log("warn", f"TTS gagal: {exc.message[:200]}")
            return False
        if not audio or not audio.get("data"):
            return False

        extension = media.extension_for(audio.get("mime") or "audio/mpeg", "mp3")
        handle, path = tempfile.mkstemp(prefix="og-voice-", suffix=f".{extension}")
        os.close(handle)
        with open(path, "wb") as file:
            file.write(audio["data"])

        async with self._speak_lock:
            try:
                for _ in range(150):
                    if voice.is_connected():
                        break
                    await asyncio.sleep(0.2)
                while voice.is_playing():
                    await asyncio.sleep(0.25)
                if not voice.is_connected():
                    return False
                voice.play(discord.FFmpegPCMAudio(path), after=lambda _exc: _remove(path))
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self._log("warn", f"Gagal memutar suara: {exc}")
                _remove(path)
                return False

    # -------------------------------------------------------------- mendengar

    def feed(self, user: Any, pcm: bytes) -> None:
        loop = getattr(self, "_loop", None)
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._feed_now, user, pcm)

    def _feed_now(self, user: Any, pcm: bytes) -> None:
        if not self._listening:
            return
        entry = self._buffers.get(user.id)
        now = time.monotonic()
        if entry is None:
            entry = {"user": user, "chunks": [], "last": now}
            self._buffers[user.id] = entry
        entry["chunks"].append(pcm)
        entry["last"] = now

    def _start_flusher(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_flusher()
        self._flusher = asyncio.create_task(self._flush_loop(), name="open-gateway-voice")

    def _stop_flusher(self) -> None:
        task = self._flusher
        self._flusher = None
        if task is not None and not task.done():
            task.cancel()

    async def _flush_loop(self) -> None:
        try:
            while self._listening:
                await asyncio.sleep(0.4)
                now = time.monotonic()
                for user_id, entry in list(self._buffers.items()):
                    pcm = b"".join(entry["chunks"])
                    seconds = duration_seconds(pcm)
                    silent_for = now - entry["last"]
                    if seconds < MIN_SPEECH_SECONDS:
                        if silent_for > SILENCE_SECONDS * 2:
                            self._buffers.pop(user_id, None)
                        continue
                    if silent_for < SILENCE_SECONDS and seconds < MAX_SPEECH_SECONDS:
                        continue
                    self._buffers.pop(user_id, None)
                    task = asyncio.create_task(self._handle_segment(entry["user"], pcm))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        except asyncio.CancelledError:
            return

    async def _handle_segment(self, user: Any, pcm: bytes) -> None:
        if self.on_transcript is None:
            return
        try:
            wav = pcm_to_wav(pcm)
            text = await media.transcribe_audio(
                self._provider_getter(), self._settings_getter(), wav, "voice.wav"
            )
        except provider.ProviderError as exc:
            self._log("warn", f"Transkripsi gagal: {exc.message[:200]}")
            return
        except Exception as exc:
            self._log("warn", f"Transkripsi gagal: {exc}")
            return
        text = (text or "").strip()
        if len(text) < 2:
            return
        self._log("info", f"Suara {getattr(user, 'display_name', user)}: {text[:160]}")
        try:
            await self.on_transcript(user, text)
        except Exception as exc:
            self._log("error", f"Gagal memproses suara: {exc}")

    def set_listening(self, enabled: bool) -> bool:
        voice = self._voice
        if voice is None or not voice.is_connected() or not VOICE_RECV_AVAILABLE:
            return False
        try:
            if enabled and not self._listening:
                voice.listen(_PcmSink(self))
                self._listening = True
                self._start_flusher()
            elif not enabled and self._listening:
                voice.stop_listening()
                self._listening = False
                self._stop_flusher()
                self._buffers.clear()
            return self._listening
        except Exception as exc:
            self.last_error = str(exc)
            self._log("warn", f"Gagal mengubah mode dengar: {exc}")
            return self._listening


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
