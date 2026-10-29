import unittest

from gateway.browser import ChatResult
from gateway.engine import GatewayEngine
from gateway.server import ChatMessage, ChatRequest

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Ambil waktu sekarang",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


class FakeAutomation:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.chats = 0
        self.thinking = []
        self.search = []

    async def new_chat(self, interactive_login=True):
        self.chats += 1

    async def send_message(
        self,
        prompt,
        thinking=None,
        search=None,
        on_update=None,
        on_reasoning=None,
        interactive_login=True,
    ):
        self.prompts.append(prompt)
        self.thinking.append(thinking)
        self.search.append(search)
        reply = self.replies.pop(0)
        if on_reasoning:
            on_reasoning("reasoning-delta")
        if on_update:
            on_update(reply)
        return ChatResult(text=reply, reasoning="reasoning-delta" if thinking else "")

    async def close(self):
        pass


def make_request(messages, model="deepseek-chat", **extra):
    return ChatRequest(model=model, messages=messages, tools=TOOLS, **extra)


class GatewayEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_call_then_tool_result_keeps_same_chat(self):
        automation = FakeAutomation(
            [
                '```tool_call\n{"name":"get_time","arguments":{}}\n```',
                "Sekarang jam 12:00.",
            ]
        )
        engine = GatewayEngine(automation=automation)
        system = ChatMessage(role="system", content="Anda asisten.")
        user = ChatMessage(role="user", content="Jam berapa?")

        first = await engine.complete(make_request([system, user]))
        self.assertIsNone(first.content)
        self.assertEqual(first.finish_reason, "tool_calls")
        self.assertEqual(len(first.tool_calls), 1)
        self.assertEqual(automation.chats, 1)
        self.assertIn("tool_call", automation.prompts[-1])

        call = first.tool_calls[0]
        assistant = ChatMessage(role="assistant", content=None, tool_calls=first.tool_calls)
        tool_result = ChatMessage(
            role="tool",
            content="12:00",
            tool_call_id=call["id"],
            name="get_time",
        )
        second = await engine.complete(make_request([system, user, assistant, tool_result]))
        self.assertEqual(second.content, "Sekarang jam 12:00.")
        self.assertEqual(second.finish_reason, "stop")
        self.assertEqual(automation.chats, 1)
        self.assertIn("HASIL TOOL", automation.prompts[-1])

    async def test_unrelated_history_opens_new_chat(self):
        automation = FakeAutomation(["Halo juga.", "Jawaban lain."])
        engine = GatewayEngine(automation=automation)
        system = ChatMessage(role="system", content="Anda asisten.")

        await engine.complete(make_request([system, ChatMessage(role="user", content="Halo")]))
        await engine.complete(make_request([system, ChatMessage(role="user", content="Pertanyaan lain")]))
        self.assertEqual(automation.chats, 2)

    async def test_thinking_follows_model_and_override(self):
        reasoner = FakeAutomation(["jawab"])
        engine = GatewayEngine(automation=reasoner)
        result = await engine.complete(
            make_request([ChatMessage(role="user", content="x")], model="deepseek-reasoner")
        )
        self.assertTrue(engine._thinking)
        self.assertEqual(result.reasoning, "reasoning-delta")

        plain = FakeAutomation(["jawab"])
        engine_plain = GatewayEngine(automation=plain)
        await engine_plain.complete(
            make_request(
                [ChatMessage(role="user", content="x")],
                model="deepseek-reasoner",
                thinking=False,
            )
        )
        self.assertFalse(engine_plain._thinking)

    async def test_search_mode_and_model_options_are_resolved(self):
        automation = FakeAutomation(["hasil pencarian", "jawaban lanjutan"])
        engine = GatewayEngine(automation=automation)
        await engine.complete(
            ChatRequest(
                model="deepseek-chat",
                messages=[ChatMessage(role="user", content="Cari berita terbaru")],
                tools=None,
                modelOptions={"thinking": True, "search": True},
            )
        )
        self.assertTrue(automation.thinking[-1])
        self.assertTrue(automation.search[-1])

        await engine.complete(
            ChatRequest(
                model="deepseek-chat",
                messages=[
                    ChatMessage(role="user", content="Cari berita terbaru"),
                    ChatMessage(role="assistant", content="hasil pencarian"),
                    ChatMessage(role="user", content="lanjutkan"),
                ],
                tools=None,
                search=False,
            )
        )
        self.assertFalse(automation.search[-1])

    async def test_string_thinking_false_is_off(self):
        automation = FakeAutomation(["jawab"])
        engine = GatewayEngine(automation=automation)
        await engine.complete(
            make_request([ChatMessage(role="user", content="x")], thinking="false")
        )
        self.assertFalse(engine._thinking)


if __name__ == "__main__":
    unittest.main()
