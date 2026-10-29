"""Prompt tool calling dan parser tool_call untuk UI chat DeepSeek."""
import json
import re
import uuid
from typing import Any, Iterable, Optional, Tuple

_TOOL_BLOCK_RE = re.compile(
    r"```[ \t]*(?:tool_call|tool-call|tool_calls|tool|function_call)[ \t]*\r?\n?(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_TOOL_XML_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_TOOL_TAG_RE = re.compile(r"<\|tool_call\|>\s*(.*?)\s*<\|/tool_call\|>", re.DOTALL)


def content_to_text(content: Any) -> str:
    """Ubah content OpenAI (string atau list part) menjadi teks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                kind = part.get("type")
                if kind in ("text", "input_text", "output_text"):
                    parts.append(str(part.get("text", "")))
                elif kind in ("image_url", "image", "input_image"):
                    parts.append("[gambar tidak didukung gateway ini]")
                elif "text" in part:
                    parts.append(str(part["text"]))
        return "\n".join(p for p in parts if p)
    return str(content)


def _tool_definitions(tools: Iterable[dict]) -> list:
    definitions = []
    for tool in tools or []:
        if isinstance(tool, dict):
            fn = tool.get("function", tool)
            if isinstance(fn, dict) and fn.get("name"):
                definitions.append(fn)
    return definitions


def tools_signature(tools: Optional[list]) -> str:
    normalized = sorted(_tool_definitions(tools), key=lambda item: item.get("name", ""))
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def tool_instruction(tools: Optional[list], tool_choice: Any = None, parallel: Optional[bool] = None) -> str:
    definitions = _tool_definitions(tools)
    if not definitions:
        return ""

    forced_name = None
    required = False
    if isinstance(tool_choice, str):
        required = tool_choice == "required"
    elif isinstance(tool_choice, dict):
        forced_name = (tool_choice.get("function") or {}).get("name")
        required = bool(forced_name) or tool_choice.get("type") == "required"

    lines = [
        "[KEMAMPUAN TOOL CALLING]",
        "Anda dapat memanggil fungsi berikut saat diperlukan:",
        json.dumps(definitions, ensure_ascii=False, separators=(",", ":")),
        "",
        "Format pemanggilan yang WAJIB dipakai (boleh satu atau beberapa blok):",
        "```tool_call",
        '{"name":"nama_tool","arguments":{"arg":"nilai"}}',
        "```",
        "",
        "- Tulis blok tool_call saja, tanpa teks pembuka/penutup, jika ingin memanggil tool.",
        "- Jangan mengarang hasil tool. Hasil akan dikirim pada pesan berikutnya.",
        "- Jika tidak perlu memanggil tool, jawab langsung tanpa blok tool_call.",
    ]
    if parallel is False:
        lines.append("- Panggil satu tool per balasan.")
    if required:
        lines.append("- Anda WAJIB memanggil minimal satu tool pada balasan ini.")
    if forced_name:
        lines.append(f"- Tool yang wajib dipanggil: {forced_name}.")
    return "\n".join(lines)


def _args_to_obj(arguments: Any) -> Any:
    if isinstance(arguments, (dict, list)):
        return arguments
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {"raw": arguments}
    return {}


def _arguments_to_json(arguments: Any) -> str:
    return json.dumps(_args_to_obj(arguments), ensure_ascii=False, separators=(",", ":"))


def _coerce_calls(obj: Any, allowed: set) -> list:
    if isinstance(obj, dict) and isinstance(obj.get("tool_calls"), list):
        candidates = obj["tool_calls"]
    elif isinstance(obj, dict) and isinstance(obj.get("function_call"), dict):
        candidates = [obj["function_call"]]
    elif isinstance(obj, list):
        candidates = obj
    else:
        candidates = [obj]

    calls = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else item
        name = fn.get("name") or item.get("tool") or item.get("tool_name")
        if not name:
            continue
        if allowed and name not in allowed:
            continue
        args = fn.get("arguments", fn.get("parameters", fn.get("args", item.get("arguments", {}))))
        calls.append(
            {
                "id": item.get("id") or "call_" + uuid.uuid4().hex[:24],
                "type": "function",
                "function": {"name": name, "arguments": _arguments_to_json(args)},
            }
        )
    return calls


def _json_candidates(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Kadang model menambahkan kalimat sebelum JSON.
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def parse_tool_calls(
    text: str,
    tools: Optional[list],
    tool_choice: Any = None,
) -> Tuple[Optional[str], list]:
    """Pisahkan teks biasa dari blok tool_call."""
    text = text or ""
    if not tools or tool_choice == "none":
        cleaned = text.strip()
        return (cleaned or None), []

    allowed = {item.get("name") for item in _tool_definitions(tools) if item.get("name")}
    calls = []
    spans = []

    for pattern in (_TOOL_BLOCK_RE, _TOOL_XML_RE, _TOOL_TAG_RE):
        for match in pattern.finditer(text):
            parsed = _json_candidates(match.group(1))
            if parsed is None:
                continue
            found = _coerce_calls(parsed, allowed)
            if found:
                calls.extend(found)
                spans.append(match.span())

    if not calls:
        stripped = text.strip()
        if stripped[:1] in ("{", "["):
            parsed = _json_candidates(stripped)
            if parsed is not None:
                calls = _coerce_calls(parsed, allowed)
                if calls:
                    return None, calls

    if not calls:
        cleaned = text.strip()
        return (cleaned or None), []

    # Buang hanya blok yang valid.
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]

    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
    return (cleaned or None), calls


def format_tool_calls_for_history(tool_calls: Optional[list]) -> str:
    lines = []
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else call
        name = fn.get("name") or call.get("name")
        args = _args_to_obj(fn.get("arguments", call.get("arguments", {})))
        lines.append(json.dumps({"name": name, "arguments": args}, ensure_ascii=False))
    return "\n".join(lines)
