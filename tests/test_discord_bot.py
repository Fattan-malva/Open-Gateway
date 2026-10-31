import unittest

from gateway.discord.bot import (
    DiscordBotManager,
    command_args,
    command_name,
    is_command,
    split_message,
    strip_prefix,
)


class FakeUser:
    def __init__(self, user_id=1):
        self.id = user_id

    def __str__(self):
        return "Bot#1"


class FakeChannel:
    def __init__(self, channel_id=10, name="general"):
        self.id = channel_id
        self.name = name


class FakeGuild:
    def __init__(self, guild_id=99):
        self.id = guild_id


class FakeMessage:
    def __init__(self, content, guild=None, mentions=None, channel=None):
        self.content = content
        self.guild = guild
        self.mentions = mentions or []
        self.channel = channel or FakeChannel()


class FakeClient:
    def __init__(self, user_id=1):
        self.user = FakeUser(user_id)
        self.latency = 0.05


class SplitMessageTests(unittest.TestCase):
    def test_empty_text_gets_placeholder(self):
        self.assertEqual(split_message("   "), ["_(kosong)_"])

    def test_short_text_is_single_chunk(self):
        self.assertEqual(split_message("halo"), ["halo"])

    def test_long_text_is_split_under_limit(self):
        text = " ".join(["kata"] * 300)
        chunks = split_message(text, limit=500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        self.assertTrue(all(chunk.strip() for chunk in chunks))

    def test_chunk_count_is_capped(self):
        chunks = split_message("x " * 10000, limit=100)
        self.assertLessEqual(len(chunks), 4)


class CommandTests(unittest.TestCase):
    def test_command_name_reads_first_word(self):
        self.assertEqual(command_name("reset"), "reset")
        self.assertEqual(command_name("!reset"), "reset")
        self.assertEqual(command_name("/ping"), "ping")
        self.assertEqual(command_name("  MODEL  "), "model")

    def test_known_commands(self):
        for text in (
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
        ):
            self.assertTrue(is_command(text))

    def test_command_args_returns_remainder(self):
        self.assertEqual(command_args("gambar kucing di bulan"), "kucing di bulan")
        self.assertEqual(command_args("join 12345"), "12345")
        self.assertEqual(command_args("help"), "")

    def test_unknown_text_is_not_command(self):
        self.assertFalse(is_command("hapus riwayat"))
        self.assertFalse(is_command(""))

    def test_strip_prefix_is_case_insensitive(self):
        self.assertEqual(strip_prefix("!AI halo", "!ai"), "halo")
        self.assertEqual(strip_prefix("!ai", "!ai"), "")
        self.assertEqual(strip_prefix("halo", "!ai"), "halo")
        self.assertEqual(strip_prefix("!aihalo", "!ai"), "!aihalo")

    def test_strip_prefix_without_prefix(self):
        self.assertEqual(strip_prefix("halo", ""), "halo")


class ShouldReplyTests(unittest.TestCase):
    def setUp(self):
        self.manager = DiscordBotManager()
        self.client = FakeClient()
        self.guild = FakeGuild()

    def settings(self, **overrides):
        base = {"trigger": "mention", "prefix": "!ai", "channel_id": ""}
        base.update(overrides)
        return base

    def test_mention_triggers_reply(self):
        message = FakeMessage("halo", guild=self.guild, mentions=[self.client.user])
        self.assertTrue(self.manager._should_reply(self.client, message, self.settings()))

    def test_plain_message_is_ignored(self):
        message = FakeMessage("halo", guild=self.guild)
        self.assertFalse(self.manager._should_reply(self.client, message, self.settings()))

    def test_dm_always_replies(self):
        message = FakeMessage("halo")
        self.assertTrue(self.manager._should_reply(self.client, message, self.settings()))

    def test_prefix_trigger(self):
        settings = self.settings(trigger="prefix")
        self.assertTrue(self.manager._should_reply(self.client, FakeMessage("!ai halo", guild=self.guild), settings))
        self.assertFalse(self.manager._should_reply(self.client, FakeMessage("halo", guild=self.guild), settings))

    def test_prefix_command_works_in_mention_mode(self):
        message = FakeMessage("!ai reset", guild=self.guild)
        self.assertTrue(self.manager._should_reply(self.client, message, self.settings()))

    def test_bare_word_is_not_a_command(self):
        message = FakeMessage("ping", guild=self.guild)
        self.assertFalse(self.manager._should_reply(self.client, message, self.settings()))

    def test_channel_trigger_requires_matching_channel(self):
        settings = self.settings(trigger="all", channel_id="10")
        self.assertTrue(self.manager._should_reply(self.client, FakeMessage("halo", guild=self.guild), settings))

        other = FakeMessage("halo", guild=self.guild, channel=FakeChannel(11))
        self.assertFalse(self.manager._should_reply(self.client, other, settings))

    def test_channel_trigger_without_channel_id(self):
        settings = self.settings(trigger="all", channel_id="")
        message = FakeMessage("halo", guild=self.guild)
        self.assertFalse(self.manager._should_reply(self.client, message, settings))

    def test_clean_text_removes_mention_and_prefix(self):
        settings = self.settings()
        message = FakeMessage("<@1> !ai tolong jelaskan", guild=self.guild, mentions=[self.client.user])
        self.assertEqual(self.manager._clean_text(self.client, message, settings), "tolong jelaskan")

    def test_clean_text_defaults_when_empty(self):
        message = FakeMessage("<@1>", guild=self.guild, mentions=[self.client.user])
        self.assertEqual(self.manager._clean_text(self.client, message, self.settings()), "Halo")


if __name__ == "__main__":
    unittest.main()
