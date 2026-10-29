import unittest

from gateway.tools import content_to_text, parse_tool_calls

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


class ContentToTextTests(unittest.TestCase):
    def test_string_and_parts(self):
        self.assertEqual(content_to_text("halo"), "halo")
        parts = [
            {"type": "text", "text": "baris 1"},
            {"type": "input_text", "text": "baris 2"},
            {"type": "image_url", "image_url": {"url": "x"}},
        ]
        text = content_to_text(parts)
        self.assertIn("baris 1", text)
        self.assertIn("baris 2", text)
        self.assertIn("gambar tidak didukung", text)


class ParseToolCallsTests(unittest.TestCase):
    def test_single_fenced_call(self):
        text, calls = parse_tool_calls(
            '```tool_call\n{"name":"get_time","arguments":{"tz":"WIB"}}\n```',
            TOOLS,
            "auto",
        )
        self.assertIsNone(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "get_time")
        self.assertEqual(calls[0]["function"]["arguments"], '{"tz":"WIB"}')

    def test_multiple_blocks_keep_surrounding_text(self):
        raw = (
            "Saya cek dulu.\n"
            '```tool_call\n{"name":"get_time","arguments":{}}\n```\n'
            '```tool_call\n{"name":"get_time","arguments":{"tz":"WIB"}}\n```'
        )
        text, calls = parse_tool_calls(raw, TOOLS, "auto")
        self.assertEqual(text, "Saya cek dulu.")
        self.assertEqual(len(calls), 2)

    def test_xml_and_bare_json(self):
        _, calls = parse_tool_calls(
            '<tool_call>{"name":"get_time","arguments":{}}</tool_call>', TOOLS, "auto"
        )
        self.assertEqual(len(calls), 1)

        text, calls = parse_tool_calls('{"name":"get_time","arguments":{}}', TOOLS, "auto")
        self.assertIsNone(text)
        self.assertEqual(len(calls), 1)

    def test_unknown_tool_is_left_as_text(self):
        raw = '```tool_call\n{"name":"unknown","arguments":{}}\n```'
        text, calls = parse_tool_calls(raw, TOOLS, "auto")
        self.assertEqual(calls, [])
        self.assertEqual(text, raw)

    def test_tool_choice_none_disables_parsing(self):
        raw = '```tool_call\n{"name":"get_time","arguments":{}}\n```'
        text, calls = parse_tool_calls(raw, TOOLS, "none")
        self.assertEqual(calls, [])
        self.assertEqual(text, raw)


if __name__ == "__main__":
    unittest.main()
