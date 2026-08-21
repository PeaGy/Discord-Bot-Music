import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import user_memory
from features.ai_chat import GrokChat


class ProvenanceStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_lookup_and_user_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "memory.db")
            with patch.object(user_memory, "DB_PATH", database):
                await user_memory.init_db()
                await user_memory.record_bot_response_provenance(
                    message_id=900,
                    channel_id=20,
                    guild_id=30,
                    requester_user_id=40,
                    requester_display_name="Dolphin",
                    source_message_id=800,
                )
                found = await user_memory.get_bot_response_provenance([900])
                self.assertEqual(found[900]["requester_user_id"], 40)
                self.assertEqual(found[900]["requester_display_name"], "Dolphin")
                self.assertEqual(found[900]["source_message_id"], 800)

                await user_memory.clear_user(40)
                self.assertEqual(
                    await user_memory.get_bot_response_provenance([900]), {}
                )


class ReplyContextAttributionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _author(user_id: int, name: str, *, bot: bool = False):
        return SimpleNamespace(id=user_id, display_name=name, bot=bot)

    async def test_bot_reply_is_attributed_to_original_requester(self):
        friend = self._author(40, "Dolphin")
        peto = self._author(99, "Peto", bot=True)
        source = SimpleNamespace(
            id=800,
            author=friend,
            clean_content="Nói kiểu dễ thương đi",
            attachments=[],
            reference=None,
        )
        bot_reply = SimpleNamespace(
            id=900,
            author=peto,
            clean_content="Một câu trả lời theo cách của Dolphin",
            attachments=[],
            reference=SimpleNamespace(message_id=800),
        )
        chat = GrokChat.__new__(GrokChat)
        chat.bot = SimpleNamespace(user=peto)

        provenance = {
            900: {
                "requester_user_id": 40,
                "requester_display_name": "Dolphin",
                "source_message_id": 800,
            }
        }
        with patch.object(
            user_memory,
            "get_bot_response_provenance",
            AsyncMock(return_value=provenance),
        ):
            context = await chat._format_message_context(
                [source, bot_reply], heading="Reply chain"
            )

        self.assertIn("được tạo để trả lời Dolphin (user_id=40)", context)
        self.assertIn("không quy cách nói/sở thích", context)

    async def test_old_reply_falls_back_to_discord_reference(self):
        friend = self._author(40, "Dolphin")
        peto = self._author(99, "Peto", bot=True)
        source = SimpleNamespace(
            id=800,
            author=friend,
            clean_content="Nói kiểu dễ thương đi",
            attachments=[],
            reference=None,
        )
        bot_reply = SimpleNamespace(
            id=900,
            author=peto,
            clean_content="Câu trả lời cũ chưa có provenance",
            attachments=[],
            reference=SimpleNamespace(message_id=800),
        )
        chat = GrokChat.__new__(GrokChat)
        chat.bot = SimpleNamespace(user=peto)

        with patch.object(
            user_memory,
            "get_bot_response_provenance",
            AsyncMock(return_value={}),
        ):
            context = await chat._format_message_context(
                [source, bot_reply], heading="Reply chain"
            )

        self.assertIn("được tạo để trả lời Dolphin (user_id=40)", context)


if __name__ == "__main__":
    unittest.main()
