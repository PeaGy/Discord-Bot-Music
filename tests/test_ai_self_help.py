import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from features.ai_chat import GrokChat, SYSTEM_PROMPT, _ToolCall
from guild_ai_settings import GuildAIPolicy
from tests.test_peto_help import make_tree


class Admission:
    def __init__(self):
        self.calls = []

    @asynccontextmanager
    async def admit(self, guild, user, policy, *, heavy):
        self.calls.append((guild, user, heavy))
        yield


def make_chat():
    chat = GrokChat.__new__(GrokChat)
    chat.bot = SimpleNamespace(tree=make_tree(), user=SimpleNamespace(id=900), is_owner=AsyncMock(return_value=False))
    chat._create_response = AsyncMock(return_value=SimpleNamespace(output_text="Vào voice rồi dùng `/play` nhé.", output=[]))
    chat._is_reply_to_bot = AsyncMock(return_value=False)
    chat.ai_admission = Admission()
    chat.ai_settings = SimpleNamespace(
        ensure=AsyncMock(return_value=GuildAIPolicy(guild_id=1)),
        list_channels=AsyncMock(return_value=set()),
        list_roles=AsyncMock(return_value=set()),
    )
    return chat


def make_message(chat, text):
    return SimpleNamespace(
        id=7, content=text, mentions=[chat.bot.user], reference=None,
        guild=SimpleNamespace(id=1),
        author=SimpleNamespace(id=2, bot=False, roles=[], display_name="Member"),
        channel=SimpleNamespace(id=3, typing=MagicMock(return_value=MagicMock())),
        attachments=[SimpleNamespace(filename="attached.png", content_type="image/png")],
        reply=AsyncMock(),
    )


class AISelfHelpTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_receives_only_public_help_and_no_tools_or_history(self):
        chat = make_chat()
        text = "Làm sao mở nhạc?"
        with patch("features.ai_chat.user_memory.get_history", side_effect=AssertionError("No private history")):
            result = await chat._ask_grok(SimpleNamespace(), text)
        self.assertIn("/play", result[0])
        self.assertEqual(result[1:], (None, None))
        args = chat._create_response.await_args.kwargs
        self.assertTrue(args["instructions"].startswith(SYSTEM_PROMPT))
        self.assertIn("/play song:<", args["instructions"])
        self.assertEqual(args["input_data"], text)
        self.assertFalse(args["use_tools"])
        self.assertFalse(args["allow_image_tools"])
        self.assertEqual(args["tool_choice"], "none")
        self.assertEqual(args["allowed_tool_names"], set())
        self.assertEqual(args["reasoning_effort"], "low")

    async def test_tool_response_or_raw_tool_text_becomes_local_guidance(self):
        cases = (
            SimpleNamespace(output_text="", output=[{"type": "function_call", "name": "play_music", "arguments": '{"query":"song"}', "call_id": "1"}]),
            SimpleNamespace(output_text='play_music(query="song")', output=[]),
            SimpleNamespace(output_text='Dùng `/skip` nhé.', output=[]),
        )
        for response in cases:
            with self.subTest(response=response):
                chat = make_chat()
                chat._create_response.return_value = response
                answer = await chat._answer_public_help("Làm sao mở nhạc?")
                self.assertIn("chưa thực hiện thao tác nào", answer)
                self.assertNotIn("play_music", answer)

    async def test_upstream_failure_still_provides_registered_commands(self):
        chat = make_chat()
        chat._create_response.side_effect = TimeoutError()
        answer = await chat._answer_public_help("Làm sao mở nhạc?")
        self.assertIn("`/play`", answer)

    async def test_help_cannot_execute_or_force_any_model_tool(self):
        chat = make_chat()
        for name in ("play_music", "skip_music", "generate_image", "edit_image", "get_danbooru_image", "search_limbus_wiki"):
            with self.subTest(tool=name):
                call = _ToolCall(name, {"query": "song"}, "1")
                text, embed, files = await chat._handle_tool_call(
                    SimpleNamespace(), call, user_text="Làm sao mở nhạc?", source_data_url="data:image/png;base64,unused"
                )
                self.assertIn("chưa thực hiện thao tác nào", text)
                self.assertIsNone(embed)
                self.assertIsNone(files)
        calls, _ = await chat._resolve_tool_calls(
            SimpleNamespace(output_text="", output=[]), user_text="Cách tạo ảnh AI với Peto?",
            system_prompt="help", input_messages=[], has_source_image=True,
        )
        self.assertEqual(calls, [])
        chat._create_response.assert_not_awaited()

    async def test_help_bypasses_media_actions_and_execution_access_gates(self):
        chat = make_chat()
        for method in ("_iter_image_attachments", "_collect_image_parts", "_iter_video_attachments",
                       "_collect_video_parts", "_collect_channel_context", "_collect_link_context",
                       "_get_edit_source_data_url", "_has_ai_capability_access"):
            setattr(chat, method, AsyncMock(side_effect=AssertionError(f"Must not call {method}")))
        chat._looks_like_asset_request = MagicMock(side_effect=AssertionError("No asset route"))
        policy = GuildAIPolicy(guild_id=1, limbus_enabled=False, image_generation_enabled=False,
                               image_read_enabled=False, music_enabled=False, web_enabled=False)
        with patch("features.ai_chat.user_memory.is_ai_blacklisted", AsyncMock(return_value=False)):
            for text in ("Cách tạo sticker?", "Gacha Limbus dùng sao?", "Làm sao mở nhạc?",
                         "Cách chỉnh ảnh này?", "Cách xóa nền ảnh?",
                         "Peto có lưu playlist không?", "Cách dùng /download https://example.org/test?"):
                with self.subTest(text=text):
                    message = make_message(chat, text)
                    await chat._handle_ai_message(message, policy=policy)
                    message.reply.assert_awaited_once()
                    self.assertFalse(message.reply.await_args.kwargs["allowed_mentions"].everyone)
                    self.assertFalse(message.reply.await_args.kwargs["mention_author"])

    async def test_help_admission_remains_light_with_attachment(self):
        chat = make_chat()
        chat._handle_ai_message = AsyncMock()
        chat._iter_image_attachments = AsyncMock(side_effect=AssertionError("No image collection"))
        chat._iter_video_attachments = AsyncMock(side_effect=AssertionError("No video collection"))
        with patch("features.ai_chat.user_memory.is_ai_blacklisted", AsyncMock(return_value=False)):
            await chat.on_message(make_message(chat, "Cách tạo ảnh AI?"))
        self.assertEqual(chat.ai_admission.calls, [(1, 2, False)])
        chat._handle_ai_message.assert_awaited_once()

    async def test_disabled_chat_still_blocks_help(self):
        chat = make_chat()
        chat.ai_settings.ensure.return_value = GuildAIPolicy(guild_id=1, response_mode="off")
        message = make_message(chat, "Làm sao mở nhạc?")
        await chat.on_message(message)
        chat._create_response.assert_not_awaited()
        self.assertIn("Chat AI", message.reply.await_args.args[0])

    async def test_context_help_never_reads_selected_message_or_media(self):
        chat = make_chat()
        interaction = SimpleNamespace(user=SimpleNamespace(id=2, roles=[]), guild_id=1, channel=SimpleNamespace(id=3))
        # A target with no attributes is deliberate: public how-to must not read it.
        with patch("features.ai_chat.user_memory.is_ai_blacklisted", AsyncMock(return_value=False)):
            answer = await chat.answer_context_message(interaction, SimpleNamespace(), "Cách dùng /play?")
        self.assertIn("/play", answer)
        self.assertEqual(chat.ai_admission.calls, [(1, 2, False)])

    async def test_direct_music_request_retains_existing_handler(self):
        chat = make_chat()
        message = make_message(chat, "Peto mở nhạc AIZO đi")
        message.author.voice = SimpleNamespace(channel=SimpleNamespace(id=4))
        with patch("music.player.play_song_by_query", AsyncMock(return_value={"ok": True, "song": {"title": "AIZO"}})) as play:
            text, _, _ = await chat._handle_tool_call(message, _ToolCall("play_music", {"query": "AIZO"}, "1"), user_text=message.content)
        play.assert_awaited_once()
        self.assertIn("AIZO", text)

    async def test_long_help_is_sent_as_complete_file_without_pings(self):
        chat = make_chat()
        message = make_message(chat, "Làm sao mở nhạc?")
        content = "Hướng dẫn sử dụng Peto.\n" * 500
        recorded = {}

        async def record(*args, **kwargs):
            recorded["bytes"] = kwargs["file"].fp.getvalue()
            recorded["mentions"] = kwargs["allowed_mentions"]

        message.reply.side_effect = record
        await chat._send_public_help(message, content)
        message.reply.assert_awaited_once()
        self.assertEqual(recorded["bytes"].decode("utf-8-sig"), content)
        self.assertFalse(recorded["mentions"].users)


if __name__ == "__main__":
    unittest.main()
