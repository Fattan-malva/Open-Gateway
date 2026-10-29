"""Mesin percakapan: menyusun prompt, mengelola sesi browser, dan tool calling."""
import asyncio
import json
import shutil
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .browser import DeepSeekBrowserAutomation
from .config import COOKIES_FILE, MAX_PROMPT_CHARS, PROFILE_DIR
from .tools import (
    content_to_text,
    format_tool_calls_for_history,
    parse_tool_calls,
    tool_instruction,
    tools_signature,
    normalize_tool_request,
)

THINKING_OFF_VALUES = {"none", "off", "0", "false", "disabled", "no"}


@dataclass
class Completion:
    content: Optional[str]
    reasoning: Optional[str]
    tool_calls: list
    finish_reason: str


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalize_messages(messages) -> list:
    normalized = []
    for message in messages:
        tool_calls = _field(message, "tool_calls", None) or []
        function_call = _field(message, "function_call", None)
        normalized.append(
            {
                "role": _field(message, "role", "user"),
                "content": content_to_text(_field(message, "content", None)),
                "tool_calls": [dict(call) if isinstance(call, dict) else call for call in tool_calls],
                "function_call": dict(function_call) if isinstance(function_call, dict) else function_call,
                "tool_call_id": _field(message, "tool_call_id", None),
                "name": _field(message, "name", None),
                "reasoning_content": _field(message, "reasoning_content", None),
            }
        )
    return normalized

def _state_message(message: dict) -> dict:
    """Bentuk yang dibandingkan untuk mendeteksi lanjutan percakapan."""
    return {
        "role": message.get("role") or "user",
        "content": message.get("content") or "",
        "tool_calls": message.get("tool_calls") or [],
        "function_call": message.get("function_call"),
        "tool_call_id": message.get("tool_call_id"),
        "name": message.get("name"),
    }

def _render_message(message: dict) -> str:
    role = message.get("role") or "user"
    content = message.get("content") or ""
    if role == "assistant":
        parts = []
        if content:
            parts.append(content)
        calls = format_tool_calls_for_history(message.get("tool_calls"))
        if calls:
            parts.append("[TOOL CALL]\n" + calls)
        legacy_call = message.get("function_call")
        if isinstance(legacy_call, dict) and legacy_call.get("name"):
            parts.append("[FUNCTION CALL - legacy]\n" + json.dumps(legacy_call, ensure_ascii=False))
        return "\n".join(parts) or "[jawaban kosong]"
    if role == "tool":
        cid = message.get("tool_call_id") or "-"
        name = message.get("name") or "-"
        return f"[HASIL TOOL call_id={cid} name={name}]\n{content}"
    if role == "function":
        name = message.get("name") or "-"
        return f"[HASIL FUNCTION legacy name={name}]\n{content}"
    if role in ("system", "developer"):
        return content
    return content

def _truncate_messages(messages: list, budget: int) -> list:
    if budget <= 0:
        return messages
    total = sum(len(message.get("content") or "") for message in messages)
    if total <= budget:
        return messages

    system_messages = [message for message in messages if message.get("role") in ("system", "developer")]
    others = [message for message in messages if message.get("role") not in ("system", "developer")]
    used = sum(len(message.get("content") or "") for message in system_messages)
    kept = []

    for message in reversed(others):
        size = len(message.get("content") or "") + 80
        if kept and used + size > budget:
            break
        kept.append(dict(message))
        used += size

    kept.reverse()
    if len(kept) < len(others):
        kept.insert(
            0,
            {
                "role": "system",
                "content": "[...riwayat percakapan lama dipotong oleh gateway...]",
                "tool_calls": [],
                "tool_call_id": None,
                "name": None,
            },
        )

    if kept:
        last = kept[-1]
        max_last = max(1000, budget - used + len(last.get("content") or ""))
        if len(last.get("content") or "") > max_last:
            last = dict(last)
            last["content"] = last["content"][:max_last] + "\n[...pesan dipotong...]"
            kept[-1] = last
    return system_messages + kept


def render_full_prompt(messages: list, tools, tool_choice, parallel: Optional[bool]) -> str:
    system_parts = [
        m["content"]
        for m in messages
        if m.get("role") in ("system", "developer") and m.get("content")
    ]
    conversation = [m for m in messages if m.get("role") not in ("system", "developer")]
    if not conversation:
        conversation = [{"role": "user", "content": "(tidak ada pesan pengguna)"}]

    current = conversation[-1]
    history = conversation[:-1]
    rules = tool_instruction(tools, tool_choice, parallel)

    blocks = [
        "[INSTRUKSI GATEWAY]\n"
        "Anda adalah model yang diakses melalui gateway OpenAI-compatible. "
        "Jawablah sebagai asisten yang membantu dan gunakan bahasa yang sama dengan pengguna."
    ]
    if rules:
        blocks.append(rules)
    if system_parts:
        blocks.append("[INSTRUKSI SISTEM]\n" + "\n\n".join(system_parts))
    if history:
        rendered_history = []
        for message in history:
            label = (message.get("role") or "user").upper()
            rendered_history.append(f"[{label}]\n{_render_message(message)}")
        blocks.append("[RIWAYAT PERCAKAPAN]\n" + "\n\n".join(rendered_history))

    blocks.append(f"[PESAN SAAT INI - {current.get('role', 'user').upper()}]\n{_render_message(current)}")
    if current.get("role") in ("tool", "function"):
        blocks.append(
            "[INSTRUKSI AKHIR]\nGunakan hasil tool di atas untuk melanjutkan. "
            "Jangan mengulang pemanggilan tool yang sama kecuali memang diperlukan."
        )
    else:
        blocks.append("[INSTRUKSI AKHIR]\nJawab pesan saat ini. Jangan menampilkan proses berpikir internal.")
    return "\n\n".join(blocks)


def render_delta_prompt(new_messages: list, tools, tool_choice, parallel: Optional[bool]) -> str:
    rules = tool_instruction(tools, tool_choice, parallel)
    rendered = []
    for message in new_messages:
        label = (message.get("role") or "user").upper()
        rendered.append(f"[{label}]\n{_render_message(message)}")

    blocks = []
    if rules:
        blocks.append(rules)
    blocks.append("[PESAN BARU]\n" + "\n\n".join(rendered))
    blocks.append(
        "[INSTRUKSI AKHIR]\nLanjutkan percakapan berdasarkan pesan baru di atas. "
        "Jangan mengulang jawaban lama."
    )
    return "\n\n".join(blocks)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        value = value.strip().lower()
        if not value:
            return False
        return value not in THINKING_OFF_VALUES
    return bool(value)


def _model_options(request) -> dict:
    options = _field(request, "modelOptions", None)
    if options is None:
        options = _field(request, "model_options", None)
    return options if isinstance(options, dict) else {}


def resolve_thinking(request) -> bool:
    for name in ("thinking", "ds_thinking", "enable_thinking"):
        value = _field(request, name, None)
        if value is not None:
            return _truthy(value)

    options = _model_options(request)
    for name in ("thinking", "ds_thinking", "enable_thinking"):
        if options.get(name) is not None:
            return _truthy(options[name])

    effort = _field(request, "reasoning_effort", None)
    reasoning = _field(request, "reasoning", None)
    if effort is None and isinstance(reasoning, dict):
        if "enabled" in reasoning:
            return _truthy(reasoning.get("enabled"))
        effort = reasoning.get("effort") or reasoning.get("level")
    elif effort is None and isinstance(reasoning, bool):
        return reasoning
    elif effort is None and isinstance(reasoning, str):
        effort = reasoning

    if effort is not None and str(effort).strip():
        return str(effort).strip().lower() not in THINKING_OFF_VALUES

    model = str(_field(request, "model", "")).lower()
    return any(marker in model for marker in ("reasoner", "thinking", "deepseek-r1", "-r1"))


def resolve_search(request) -> bool:
    for name in ("search", "web_search", "enable_search"):
        value = _field(request, name, None)
        if value is not None:
            return _truthy(value)

    options = _model_options(request)
    for name in ("search", "web_search", "enable_search"):
        if options.get(name) is not None:
            return _truthy(options[name])
    return False


def _remove_profile_files():
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
    try:
        COOKIES_FILE.unlink()
    except (FileNotFoundError, OSError):
        pass


class GatewayEngine:
    def __init__(self, automation: Optional[DeepSeekBrowserAutomation] = None):
        self._automation = automation
        self._lock: Optional[asyncio.Lock] = None
        self._sent_messages: list = []
        self._tools_sig = ""
        self._thinking: Optional[bool] = None
        self._browser_state = "ready" if automation is not None else "stopped"
        self._logged_in = automation is not None
        self._last_error: Optional[str] = None
        self._login_task: Optional[asyncio.Task] = None

    def _get_lock(self) -> asyncio.Lock:
        # Dibuat lazy agar aman dipakai di event loop uvicorn/TestClient mana pun.
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _ensure_browser(self, interactive: bool = False) -> DeepSeekBrowserAutomation:
        if self._automation is not None and getattr(self._automation, "running", True):
            return self._automation
        await self._stop_automation()
        self._browser_state = "starting"
        self._last_error = None
        automation = DeepSeekBrowserAutomation()
        try:
            await automation.start()
            await automation.login(interactive=interactive)
        except Exception as exc:
            self._browser_state = "error"
            self._last_error = str(exc)
            self._logged_in = False
            try:
                await automation.close()
            except Exception:
                pass
            raise
        self._automation = automation
        self._logged_in = True
        self._browser_state = "ready"
        return automation

    async def _stop_automation(self):
        if self._automation is not None:
            try:
                await self._automation.close()
            finally:
                self._automation = None
        self._logged_in = False

    async def login(self):
        """Login manual; dipanggil di background oleh web UI."""
        async with self._get_lock():
            self._last_error = None
            self._browser_state = "starting"
            try:
                if self._automation is not None and getattr(self._automation, "running", True):
                    if await self._automation.is_logged_in():
                        self._logged_in = True
                        self._browser_state = "ready"
                        return
                    if getattr(self._automation, "headless", False):
                        await self._stop_automation()

                if self._automation is None or not getattr(self._automation, "running", True):
                    await self._stop_automation()
                    automation = DeepSeekBrowserAutomation()
                    # Login manual selalu butuh jendela terlihat.
                    await automation.start(headless=False)
                    self._automation = automation

                self._browser_state = "waiting_login"
                await self._automation.login(interactive=True)
            except Exception as exc:
                self._browser_state = "error"
                self._last_error = str(exc)
                self._logged_in = False
                raise
            self._logged_in = True
            self._browser_state = "ready"
            self._last_error = None

    def start_login(self) -> bool:
        if self._login_task is not None and not self._login_task.done():
            return False
        self._login_task = asyncio.create_task(self._login_worker())
        return True

    async def _login_worker(self):
        try:
            await self.login()
        except Exception:
            pass

    async def browser_status(self) -> dict:
        automation = self._automation
        running = bool(automation is not None and getattr(automation, "running", True))
        return {
            "state": self._browser_state,
            "running": running,
            "logged_in": bool(self._logged_in and running),
            "busy": self._get_lock().locked(),
            "headless": bool(getattr(automation, "headless", False)) if running else None,
            "profile_dir": str(PROFILE_DIR),
            "profile_exists": PROFILE_DIR.exists(),
            "cookies_file": str(COOKIES_FILE),
            "cookies_exists": COOKIES_FILE.exists(),
            "last_error": self._last_error,
        }

    async def close_browser(self):
        async with self._get_lock():
            await self._stop_automation()
            self._browser_state = "stopped"
            self._last_error = None

    async def restart_browser(self):
        async with self._get_lock():
            await self._stop_automation()
            self._browser_state = "stopped"
            self._last_error = None
            await self._ensure_browser(interactive=False)

    async def reset_profile(self):
        async with self._get_lock():
            await self._stop_automation()
            await asyncio.to_thread(_remove_profile_files)
            self._sent_messages = []
            self._tools_sig = ""
            self._thinking = None
            self._browser_state = "stopped"
            self._last_error = None

    async def reset_conversation(self):
        async with self._get_lock():
            self._sent_messages = []
            self._tools_sig = ""
            self._thinking = None

    async def close(self):
        task = self._login_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        self._login_task = None
        await self._stop_automation()
        self._sent_messages = []
        self._tools_sig = ""
        self._thinking = None
        self._browser_state = "stopped"
        self._lock = None

    async def complete(
        self,
        request,
        on_content: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
    ) -> Completion:
        async with self._get_lock():
            try:
                result = await self._complete_locked(request, on_content, on_reasoning)
            except Exception as exc:
                # Browser mungkin sudah menerima prompt walau respons gagal dikirim.
                # Reset cache agar request selanjutnya membangun ulang history klien.
                self._sent_messages = []
                self._tools_sig = ""
                self._thinking = None
                self._last_error = str(exc)
                message = str(exc).lower()
                if any(marker in message for marker in ("login", "sign_in", "sesi")):
                    self._logged_in = False
                    self._browser_state = "error"
                raise
            self._last_error = None
            return result

    async def _complete_locked(
        self,
        request,
        on_content: Optional[Callable[[str], None]],
        on_reasoning: Optional[Callable[[str], None]],
    ) -> Completion:
        messages = _normalize_messages(_field(request, "messages", []) or [])
        if not messages:
            raise ValueError("messages tidak boleh kosong")
        if not any(message["role"] in ("user", "tool", "function") for message in messages):
            raise ValueError("Tidak ada pesan user/tool")

        tools, tool_choice, parallel, legacy_functions = normalize_tool_request(
            tools=_field(request, "tools", None),
            tool_choice=_field(request, "tool_choice", None),
            parallel=_field(request, "parallel_tool_calls", None),
            functions=_field(request, "functions", None),
            function_call=_field(request, "function_call", None),
        )
        tools_sig = tools_signature(tools)

        incoming_state = [_state_message(message) for message in messages]
        prefix_ok = (
            bool(self._sent_messages)
            and tools_sig == self._tools_sig
            and incoming_state[: len(self._sent_messages)] == self._sent_messages
        )
        delta_messages = messages[len(self._sent_messages) :] if prefix_ok else []

        if prefix_ok and (not delta_messages or any(m["role"] == "assistant" for m in delta_messages)):
            prefix_ok = False

        automation = await self._ensure_browser()
        if prefix_ok:
            prompt = render_delta_prompt(delta_messages, tools, tool_choice, parallel)
        else:
            await automation.new_chat(interactive_login=False)
            prompt = render_full_prompt(
                _truncate_messages(messages, MAX_PROMPT_CHARS), tools, tool_choice, parallel
            )

        thinking = resolve_thinking(request)
        search = resolve_search(request)
        result = await automation.send_message(
            prompt,
            thinking=thinking,
            search=search,
            on_update=on_content,
            on_reasoning=on_reasoning,
            interactive_login=False,
        )
        content, tool_calls = parse_tool_calls(result.text, tools, tool_choice, parallel)

        forced_tool = tool_choice == "required" or (
            isinstance(tool_choice, dict)
            and bool((tool_choice.get("function") or {}).get("name"))
        )
        if tools and forced_tool and not tool_calls:
            # Strict choices must not silently degrade into a normal text answer.
            # The underlying website has no native tool-call channel, so ask once
            # for a corrected envelope and only return it if the parser validates it.
            correction = (
                "[TOOL CALL FORMAT CORRECTION]\n"
                "Your previous answer did not contain a valid tool call. Re-read the original user request and the function definitions below. "
                "Return only one complete tool_call block with a registered function name and arguments as a valid JSON object. "
                "Do not answer in natural language and do not invent tool results.\n\n"
                + tool_instruction(tools, tool_choice, parallel)
            )
            retry = await automation.send_message(
                correction,
                thinking=thinking,
                search=search,
                on_update=None,
                on_reasoning=None,
                interactive_login=False,
            )
            retry_content, retry_calls = parse_tool_calls(
                retry.text, tools, tool_choice, parallel
            )
            if not retry_calls:
                raise RuntimeError(
                    "DeepSeek tidak menghasilkan tool call valid setelah satu percobaan koreksi; "
                    "coba tool_choice=auto atau perjelas deskripsi/schema tool."
                )
            result = retry
            content, tool_calls = retry_content, retry_calls

        reasoning = result.reasoning if thinking and result.reasoning else None

        assistant = {
            "role": "assistant",
            "content": content or "",
            "tool_calls": tool_calls,
            "function_call": None,
            "tool_call_id": None,
            "name": None,
            "reasoning_content": reasoning,
        }
        if legacy_functions and tool_calls:
            first = tool_calls[0]["function"]
            assistant["tool_calls"] = []
            assistant["function_call"] = {
                "name": first["name"],
                "arguments": first["arguments"],
            }
        self._sent_messages = incoming_state + [_state_message(assistant)]
        self._tools_sig = tools_sig
        self._thinking = thinking

        return Completion(
            content=content,
            reasoning=reasoning,
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
        )
