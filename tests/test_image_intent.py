import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from features.ai_chat import GrokChat, XAI_RESPONSE_TIMEOUTS


class ImageEditIntentTests(unittest.TestCase):
    def test_polish_text_is_not_image_edit(self):
        text = (
            "Câu what are solutions to traffic jams. Peto đưa ra 2 giải pháp "
            "và kèm theo lời giải thích cho nó chỉnh chu hơn tí"
        )
        self.assertFalse(GrokChat._user_wants_edit_image(text))
        self.assertFalse(GrokChat._should_edit_with_source(text, True))

    def test_explicit_image_edits_are_detected(self):
        samples = (
            "Peto chỉnh ảnh này sáng hơn giúp tôi",
            "peto chinh anh nay sang hon",
            "thêm chữ Peto vào ảnh",
            "xóa nền trắng",
            "edit this",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertTrue(GrokChat._user_wants_edit_image(text))

    def test_general_text_changes_are_not_image_edits(self):
        samples = (
            "thêm hai giải pháp và giải thích kỹ hơn",
            "đổi câu trả lời sang tiếng Anh",
            "sửa ngữ pháp cho đoạn này",
            "viết cho chỉnh chu hơn",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertFalse(GrokChat._user_wants_edit_image(text))


class ResponseGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_casual_request_does_not_expose_image_tools(self):
        captured = {}

        async def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(usage=None)

        chat = GrokChat.__new__(GrokChat)
        chat.client = SimpleNamespace(responses=SimpleNamespace(create=create))
        chat._prepare_client = AsyncMock()

        await chat._create_response(
            instructions="chat",
            input_data="hello",
            allow_image_tools=False,
            reasoning_effort="low",
        )

        names = {tool.get("name") for tool in captured["tools"]}
        self.assertNotIn("generate_image", names)
        self.assertNotIn("edit_image", names)
        self.assertNotIn("get_danbooru_image", names)

    async def test_low_effort_timeout_retries_only_once(self):
        calls = 0

        async def create(**kwargs):
            nonlocal calls
            calls += 1
            await __import__("asyncio").sleep(1)

        chat = GrokChat.__new__(GrokChat)
        chat.client = SimpleNamespace(responses=SimpleNamespace(create=create))
        chat._prepare_client = AsyncMock()

        with patch.dict(XAI_RESPONSE_TIMEOUTS, {"low": 0.01}):
            with self.assertRaises(TimeoutError):
                await chat._create_response(
                    instructions="chat",
                    input_data="hello",
                    use_tools=False,
                    reasoning_effort="low",
                )
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
