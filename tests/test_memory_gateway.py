import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web

from features.memory_gateway import WEB_SCOPE, MemoryGateway

TOKEN = "token-test-khong-that"
USER_ID = "447975972147298305"


def make_gateway(token: str = TOKEN) -> MemoryGateway:
    gateway = MemoryGateway.__new__(MemoryGateway)
    gateway.bot = SimpleNamespace()
    gateway.token = token
    gateway.enabled = bool(token)
    gateway.requests = {}
    gateway.runner = None
    gateway.site = None
    import asyncio
    from collections import defaultdict, deque

    gateway.requests = defaultdict(deque)
    gateway.lock = asyncio.Lock()
    return gateway


def make_request(*, token: str | None = TOKEN, user_id: str = USER_ID) -> web.Request:
    headers = {"X-Peto-Token": token} if token is not None else {}
    return SimpleNamespace(
        headers=headers,
        match_info={"user_id": user_id},
        remote="127.0.0.1",
    )


class MemoryGatewayAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_token_is_rejected(self):
        response = await make_gateway()._memory(make_request(token=None))
        self.assertEqual(response.status, 401)

    async def test_wrong_token_is_rejected(self):
        response = await make_gateway()._memory(make_request(token="sai-token"))
        self.assertEqual(response.status, 401)

    async def test_gateway_without_token_rejects_everything(self):
        # Cog tắt thì không có token nào mở được cửa.
        response = await make_gateway(token="")._memory(make_request(token=""))
        self.assertEqual(response.status, 401)

    async def test_disabled_gateway_opens_no_port(self):
        gateway = make_gateway(token="")
        await gateway.cog_load()
        self.assertFalse(gateway.enabled)
        self.assertIsNone(gateway.site)
        self.assertIsNone(gateway.runner)

    async def test_non_loopback_host_refuses_to_start(self):
        gateway = make_gateway()
        with patch("features.memory_gateway.MEMORY_HOST", "0.0.0.0"):
            await gateway.cog_load()
        self.assertFalse(gateway.enabled)
        self.assertIsNone(gateway.site)


class MemoryGatewayReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_summary_and_explicit_notes(self):
        with (
            patch(
                "user_memory.is_anonymous_mode", AsyncMock(return_value=False)
            ) as anon,
            patch(
                "user_memory.get_summary", AsyncMock(return_value="Thích Limbus.")
            ) as summary,
            patch(
                "user_memory.get_explicit_memories",
                AsyncMock(return_value=["Đang ôn thi."]),
            ),
        ):
            response = await make_gateway()._memory(make_request())

        self.assertEqual(response.status, 200)
        # json_response escape Unicode, nên phải parse chứ không so chuỗi thô.
        payload = json.loads(response.body.decode("utf-8"))
        self.assertTrue(payload["available"])
        self.assertEqual(payload["summary"], "Thích Limbus.")
        self.assertEqual(payload["explicit"], ["Đang ôn thi."])
        # Trí nhớ phải được tra đúng người được hỏi, ở scope riêng của web.
        anon.assert_awaited_once_with(int(USER_ID), WEB_SCOPE)
        summary.assert_awaited_once_with(int(USER_ID), WEB_SCOPE)

    async def test_anonymous_mode_returns_nothing(self):
        with (
            patch("user_memory.is_anonymous_mode", AsyncMock(return_value=True)),
            patch(
                "user_memory.get_summary", AsyncMock(return_value="KHONG_DUOC_LO")
            ) as summary,
            patch(
                "user_memory.get_explicit_memories",
                AsyncMock(return_value=["KHONG_DUOC_LO"]),
            ),
        ):
            response = await make_gateway()._memory(make_request())

        payload = response.body.decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertNotIn("KHONG_DUOC_LO", payload)
        self.assertIn("anonymous", payload)
        # Ẩn danh thì không được đọc trí nhớ dài hạn.
        summary.assert_not_awaited()

    async def test_no_memory_reports_unavailable(self):
        with (
            patch("user_memory.is_anonymous_mode", AsyncMock(return_value=False)),
            patch("user_memory.get_summary", AsyncMock(return_value=None)),
            patch("user_memory.get_explicit_memories", AsyncMock(return_value=[])),
        ):
            response = await make_gateway()._memory(make_request())

        self.assertIn('"available": false', response.body.decode("utf-8"))

    async def test_database_error_does_not_leak_details(self):
        with (
            patch("user_memory.is_anonymous_mode", AsyncMock(return_value=False)),
            patch(
                "user_memory.get_summary",
                AsyncMock(side_effect=RuntimeError("chi tiet noi bo")),
            ),
            patch("user_memory.get_explicit_memories", AsyncMock(return_value=[])),
        ):
            response = await make_gateway()._memory(make_request())

        self.assertEqual(response.status, 500)
        self.assertNotIn("chi tiet noi bo", response.body.decode("utf-8"))


class MemoryGatewayInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_user_ids_are_rejected(self):
        for raw in ["", "abc", "12", "0" * 21, "12a45678901234567", "-1234567890123456"]:
            with self.subTest(raw=raw):
                response = await make_gateway()._memory(make_request(user_id=raw))
                self.assertEqual(response.status, 400)

    async def test_rate_limit_kicks_in(self):
        gateway = make_gateway()
        with (
            patch("user_memory.is_anonymous_mode", AsyncMock(return_value=False)),
            patch("user_memory.get_summary", AsyncMock(return_value="x")),
            patch("user_memory.get_explicit_memories", AsyncMock(return_value=[])),
            patch("features.memory_gateway.MAX_REQUESTS_PER_MINUTE", 3),
        ):
            statuses = [
                (await gateway._memory(make_request())).status for _ in range(5)
            ]
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertEqual(statuses[3:], [429, 429])


def _code_without_docstring() -> str:
    """Phần code thật của module, đã bỏ docstring đầu file.

    Docstring có nhắc tên các hàm bị cấm để giải thích lý do, nên phải loại ra
    trước khi quét, không thì test bắt nhầm chính lời giải thích.
    """
    import inspect

    import features.memory_gateway as module

    source = inspect.getsource(module)
    if module.__doc__:
        source = source.split('"""', 2)[-1]
    return source


class MemoryGatewayContractTests(unittest.TestCase):
    def test_no_write_routes_exist(self):
        """Cổng này phải chỉ đọc — không được có route ghi."""
        code = _code_without_docstring()
        for forbidden in ("add_post", "add_put", "add_delete", "set_summary",
                          "add_message", "set_anonymous_mode"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, code)

    def test_does_not_expose_raw_chat_history(self):
        """Chỉ trả tóm tắt; không mang nguyên văn tin nhắn DM sang web."""
        code = _code_without_docstring()
        for forbidden in ("get_history", "chat_history", "get_recent_for_user",
                          "search_user_history"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, code)

    def test_setup_hook_exists_for_extension_loader(self):
        import features.memory_gateway as module

        self.assertTrue(callable(module.setup))


if __name__ == "__main__":
    unittest.main()
