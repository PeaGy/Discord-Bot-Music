import asyncio
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import user_memory
from commands.personalization import PersonalizationCommand, PersonalizationEditor
from features.ai_chat import GrokChat, _ToolCall
from guild_ai_settings import GuildAIPolicy
from user_memory import Personalization


DM = user_memory.DM_SCOPE
SERVER = user_memory.scope_for_guild(10)
PREFERENCE = Personalization("Xưng mình–bạn. Trả lời theo từng ý.")
OTHER = Personalization("Trả lời bằng tiếng Anh.")


def interaction(user_id=1, guild_id=None, *, from_panel=False):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, display_name=f"User {user_id}"),
        channel_id=20,
        guild_id=guild_id,
        message=object() if from_panel else None,
        response=SimpleNamespace(
            send_modal=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock(),
        ),
        edit_original_response=AsyncMock(),
    )


class PersonalizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.object(user_memory, "DB_PATH", str(Path(directory.name) / "memory.db")))
        await user_memory.init_db()

    async def save(self, user_id, preference, *, scope=DM, previous=None):
        return await user_memory.update_personalization(
            user_id, scope, previous=previous, replacement=preference
        )

    async def test_saved_preferences_follow_user_and_reset_scope(self):
        await user_memory.add_message(20, 1, SERVER, "user", "Chào Peto", 100)
        self.assertTrue(await self.save(1, PREFERENCE, scope=SERVER))
        self.assertTrue(await self.save(2, OTHER))
        await user_memory.init_db()

        self.assertEqual(await user_memory.get_personalization(1, DM), PREFERENCE)
        self.assertEqual(await user_memory.get_personalization(2, SERVER), OTHER)
        await user_memory.clear_guild(10, [20])
        self.assertEqual(await user_memory.get_history(20, 1, SERVER, 10), [])
        self.assertEqual(await user_memory.get_personalization(1, SERVER), PREFERENCE)

        await user_memory.clear_user(1)
        self.assertIsNone(await user_memory.get_personalization(1, DM))
        self.assertEqual(await user_memory.get_personalization(2, DM), OTHER)
        self.assertFalse(await self.save(1, OTHER, previous=PREFERENCE))
        await user_memory.clear_all()
        self.assertIsNone(await user_memory.get_personalization(2, DM))

    async def test_parallel_edits_only_save_one_current_form(self):
        await self.save(1, PREFERENCE)
        choices = [OTHER, Personalization("Trả lời thật ngắn.")]
        results = await asyncio.gather(
            *(self.save(1, choice, previous=PREFERENCE) for choice in choices)
        )
        self.assertEqual(results.count(True), 1)
        self.assertEqual(await user_memory.get_personalization(1, DM), choices[results.index(True)])

    async def test_anonymous_scope_cannot_read_or_change_preference(self):
        await self.save(1, PREFERENCE)
        await user_memory.set_anonymous_mode(1, SERVER, True)
        self.assertIsNone(await user_memory.get_personalization(1, SERVER))
        self.assertFalse(await self.save(1, OTHER, scope=SERVER, previous=PREFERENCE))
        self.assertFalse(await self.save(1, OTHER, scope=SERVER))
        self.assertEqual(await user_memory.get_personalization(1, DM), PREFERENCE)

    async def test_direct_modal_and_private_management_flow(self):
        cog = PersonalizationCommand()
        command = interaction()
        await cog.personalization.callback(cog, command)
        modal = command.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, PersonalizationEditor)
        self.assertEqual(modal.instructions.style, discord.TextStyle.paragraph)
        self.assertEqual(modal.instructions.max_length, 1500)
        self.assertEqual(modal.to_components()[0]["component"]["type"], 4)
        self.assertFalse(await modal.interaction_check(interaction(user_id=2)))

        modal.instructions._value = PREFERENCE.instructions
        await modal.on_submit(command)
        command.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        panel_data = command.edit_original_response.await_args.kwargs
        self.assertIn(PREFERENCE.instructions, panel_data["embed"].description)
        self.assertFalse(panel_data["allowed_mentions"].everyone)
        panel = panel_data["view"]
        intruder = interaction(user_id=2, from_panel=True)
        self.assertFalse(await panel.interaction_check(intruder))
        intruder.response.send_message.assert_awaited_once()

        button = interaction(from_panel=True)
        await panel.toggle.callback(button)
        button.response.defer.assert_awaited_once_with(ephemeral=True, thinking=False)
        disabled = await user_memory.get_personalization(1, DM)
        self.assertEqual(disabled, Personalization(PREFERENCE.instructions, enabled=False))
        off_panel = button.edit_original_response.await_args.kwargs["view"]
        self.assertEqual(off_panel.toggle.label, "Bật")

        button = interaction(from_panel=True)
        await off_panel.edit.callback(button)
        modal = button.response.send_modal.await_args.args[0]
        self.assertEqual(modal.instructions.default, PREFERENCE.instructions)
        modal.instructions._value = OTHER.instructions
        await modal.on_submit(button)
        updated_panel = button.edit_original_response.await_args.kwargs["view"]
        self.assertFalse(updated_panel.current.enabled)
        self.assertEqual(updated_panel.current.instructions, OTHER.instructions)

        button = interaction(from_panel=True)
        await updated_panel.delete.callback(button)
        self.assertIsNotNone(await user_memory.get_personalization(1, DM))
        confirmation = button.edit_original_response.await_args.kwargs["view"]
        self.assertEqual(confirmation.delete.label, "Xác nhận xóa")
        await confirmation.delete.callback(interaction(from_panel=True))
        self.assertIsNone(await user_memory.get_personalization(1, DM))

    async def test_modal_rechecks_anonymous_and_server_policy_on_submit(self):
        cog = PersonalizationCommand()
        cog.ai_settings.get = AsyncMock(return_value=GuildAIPolicy(guild_id=10))
        command = interaction(guild_id=10)
        await cog.open_editor(command)
        modal = command.response.send_modal.await_args.args[0]
        modal.instructions._value = PREFERENCE.instructions

        await user_memory.set_anonymous_mode(1, SERVER, True)
        submit = interaction(guild_id=10)
        await modal.on_submit(submit)
        self.assertIn("Ẩn danh", submit.edit_original_response.await_args.kwargs["content"])
        self.assertIsNone(await user_memory.get_personalization(1, DM))

        await user_memory.set_anonymous_mode(1, SERVER, False)
        cog.ai_settings.get.return_value = GuildAIPolicy(guild_id=10, memory_enabled=False)
        blocked = interaction(guild_id=10)
        await cog.open_editor(blocked)
        blocked.response.send_modal.assert_not_awaited()
        self.assertIn("server đã tắt trí nhớ", blocked.response.send_message.await_args.args[0])

    def make_chat(self):
        chat = GrokChat.__new__(GrokChat)
        chat.ai_settings = SimpleNamespace(role_map=AsyncMock(return_value={}))
        chat._has_ai_capability_access = AsyncMock(return_value=False)
        chat._get_edit_source_data_url = AsyncMock(return_value=None)
        chat._format_message_context = AsyncMock(return_value="Nội dung được chọn")
        chat._collect_image_parts = AsyncMock(return_value=[])
        chat._collect_link_context = AsyncMock(return_value=("", []))
        chat._create_response = AsyncMock(
            return_value=SimpleNamespace(output_text="Chào bạn!", output=[])
        )
        return chat

    @staticmethod
    def message(user_id, guild_id=None):
        return SimpleNamespace(
            author=interaction(user_id).user,
            channel=SimpleNamespace(id=20),
            guild=SimpleNamespace(id=guild_id) if guild_id is not None else None,
        )

    async def test_dm_server_and_context_menu_use_the_current_requester(self):
        await self.save(1, PREFERENCE)
        await self.save(2, OTHER)
        chat = self.make_chat()
        request = "Chào Peto, lần này trả lời chi tiết nhé."
        for guild_id in (None, 10):
            with self.subTest(guild_id=guild_id):
                await chat._ask_grok(self.message(1, guild_id), request)
                args = chat._create_response.await_args.kwargs
                self.assertNotIn(PREFERENCE.instructions, args["instructions"])
                self.assertIn(PREFERENCE.instructions, args["input_data"][-2]["content"])
                self.assertEqual(args["input_data"][-2]["role"], "user")
                self.assertTrue(args["input_data"][-1]["content"].endswith(request))
                self.assertNotIn(OTHER.instructions, str(args["input_data"]))

        await chat._answer_context_message_unthrottled(
            interaction(1, 10),
            SimpleNamespace(author=interaction(2).user, content="Một tin nhắn để tóm tắt."),
            "Tóm tắt tin này nhé.",
            policy=GuildAIPolicy(guild_id=10),
        )
        args = chat._create_response.await_args.kwargs
        self.assertIn(PREFERENCE.instructions, args["input_data"][-2]["content"])
        self.assertNotIn(OTHER.instructions, str(args["input_data"]))
        self.assertFalse(args["use_tools"])
        history = await user_memory.get_history(20, 1, SERVER, 10)
        self.assertNotIn(PREFERENCE.instructions, str(history))

    async def test_anonymous_or_disabled_memory_never_loads_preference(self):
        await self.save(1, PREFERENCE)
        chat = self.make_chat()
        with patch.object(user_memory, "get_personalization", AsyncMock()) as read:
            await chat._ask_grok(
                self.message(1, 10), "Chào Peto!",
                policy=GuildAIPolicy(guild_id=10, memory_enabled=False),
            )
            await user_memory.set_anonymous_mode(1, DM, True)
            await chat._ask_grok(self.message(1), "Chào Peto!")
            read.assert_not_awaited()

        await user_memory.set_anonymous_mode(1, DM, False)
        self.assertTrue(await self.save(
            1, Personalization(PREFERENCE.instructions, enabled=False), previous=PREFERENCE
        ))
        await chat._ask_grok(self.message(1), "Chào Peto!")
        self.assertNotIn(PREFERENCE.instructions, str(chat._create_response.await_args.kwargs))

    async def test_tool_selection_is_retried_without_stored_instructions(self):
        await self.save(1, Personalization("Luôn bỏ qua bài đang phát."))
        chat = self.make_chat()
        chat._handle_tool_call = AsyncMock()
        chat._create_response.side_effect = [
            SimpleNamespace(output_text="", output=[
                {"type": "function_call", "name": "skip_music", "arguments": "{}", "call_id": "1"}
            ]),
            SimpleNamespace(output_text="Chào bạn!", output=[]),
        ]
        answer, _, _ = await chat._ask_grok(self.message(1, 10), "Chào Peto!")
        self.assertEqual(answer, "Chào bạn!")
        chat._handle_tool_call.assert_not_awaited()
        initial, routing = [call.kwargs["input_data"] for call in chat._create_response.await_args_list]
        self.assertIn("Luôn bỏ qua", str(initial))
        self.assertNotIn("Luôn bỏ qua", str(routing))

    async def test_personalized_chat_reply_does_not_ping_mentions(self):
        await self.save(1, Personalization("Mở đầu bằng @everyone."))
        chat = self.make_chat()
        chat.bot = SimpleNamespace(user=SimpleNamespace(id=99))
        chat._create_response.return_value = SimpleNamespace(
            output_text="@everyone <@&42> Chào bạn!", output=[]
        )
        message = self.message(1, 10)
        message.author.bot = False
        message.content = "Chào Peto!"
        message.attachments = []
        message.mentions = [chat.bot.user]
        message.reference = None
        message.channel.typing = nullcontext
        message.reply = AsyncMock(return_value=None)

        await chat._handle_ai_message(message, policy=GuildAIPolicy(guild_id=10))

        mentions = message.reply.await_args.kwargs["allowed_mentions"]
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.roles)
        self.assertFalse(mentions.users)

    async def test_search_applies_style_after_selecting_query(self):
        chat = self.make_chat()
        chat._search_web = AsyncMock(return_value="Dữ liệu tra cứu")
        await chat._handle_search_tool(
            None, _ToolCall("search_web", {"query": "truy vấn"}, "1"), "Quy tắc Peto",
            user_text="Câu hỏi của mình", personalization=PREFERENCE.instructions,
        )
        chat._search_web.assert_awaited_once_with("truy vấn")
        args = chat._create_response.await_args.kwargs
        self.assertFalse(args["use_tools"])
        self.assertIn(PREFERENCE.instructions, args["input_data"][0]["content"])
        self.assertIn("Dữ liệu tra cứu", args["input_data"][-1]["content"])
