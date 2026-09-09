"""Cổng đọc trí nhớ nội bộ cho Peto Web.

Peto Web (ứng dụng web riêng, chạy cùng VPS) cần biết trí nhớ dài hạn của
người đang đăng nhập để giữ tính liên tục giữa Discord và web. Module này mở
một HTTP endpoint **chỉ đọc**, **chỉ nghe loopback**, trả về trí nhớ của đúng
MỘT người mỗi lượt.

Vì sao không dùng chung `features/download_gateway.py`: gateway đó được
Cloudflare Tunnel cố ý công khai ra Internet. Trí nhớ cá nhân không được đi
qua một service đang mở công khai, nên đây là site riêng và không bao giờ
được đặt sau tunnel.

Bất biến:
- Mặc định TẮT. Không có `MEMORY_GATEWAY_TOKEN` thì không mở cổng nào.
- Chỉ đọc. Không có route nào ghi vào `bot_memory.db`.
- Mỗi lượt một `user_id`. Không có route liệt kê toàn bộ người dùng.
- Chỉ trả bản tóm tắt và ghi chú đã ghim; KHÔNG trả `chat_history` thô, để
  không mang nguyên văn tin nhắn riêng ở DM sang bề mặt khác.
- Chế độ ẩn danh không đọc trí nhớ dài hạn (AGENTS.md, "Persistent data").
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from collections import defaultdict, deque

from aiohttp import web
from discord.ext import commands

import user_memory

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        logger.warning("%s không hợp lệ; dùng mặc định %s", name, default)
        return default


MEMORY_HOST = os.getenv("MEMORY_GATEWAY_HOST", "127.0.0.1").strip()
MEMORY_PORT = _env_int("MEMORY_GATEWAY_PORT", 8766, 1)
MEMORY_TOKEN = (os.getenv("MEMORY_GATEWAY_TOKEN") or "").strip()
MAX_REQUESTS_PER_MINUTE = _env_int("MEMORY_GATEWAY_MAX_REQUESTS_PER_MINUTE", 60, 1)

# Web chỉ đọc, nhưng vẫn cần một scope để tra chế độ ẩn danh. Dùng scope riêng
# để việc bật/tắt ẩn danh trên web không đụng tới DM hay server nào.
WEB_SCOPE = "web"

# Discord snowflake nằm trong khoảng 17-20 chữ số.
_MIN_ID_LEN = 17
_MAX_ID_LEN = 20


class MemoryGateway(commands.Cog):
    """HTTP loopback chỉ-đọc, phục vụ Peto Web trên cùng máy."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.token = MEMORY_TOKEN
        self.enabled = bool(self.token)
        self.requests: dict[str, deque[float]] = defaultdict(deque)
        self.lock = asyncio.Lock()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    # -- vòng đời -----------------------------------------------------------

    async def cog_load(self) -> None:
        if not self.enabled:
            logger.info(
                "Memory Gateway tắt (chưa đặt MEMORY_GATEWAY_TOKEN). "
                "Peto Web sẽ chạy mà không có trí nhớ từ Discord."
            )
            return

        if MEMORY_HOST not in {"127.0.0.1", "::1", "localhost"}:
            self.enabled = False
            logger.error(
                "Memory Gateway từ chối mở trên host %s — chỉ cho phép loopback.",
                MEMORY_HOST,
            )
            return

        app = web.Application(client_max_size=1024)
        app.router.add_get("/health", self._health)
        app.router.add_get("/internal/memory/{user_id}", self._memory)
        self.runner = web.AppRunner(app, access_log=None, handle_signals=False)
        try:
            await self.runner.setup()
            self.site = web.TCPSite(
                self.runner,
                host=MEMORY_HOST,
                port=MEMORY_PORT,
                shutdown_timeout=10,
            )
            await self.site.start()
        except Exception:
            self.enabled = False
            if self.runner:
                await self.runner.cleanup()
            self.runner = None
            self.site = None
            logger.exception(
                "Không thể mở Memory Gateway tại http://%s:%s",
                MEMORY_HOST,
                MEMORY_PORT,
            )
            return

        logger.info(
            "Memory Gateway sẵn sàng (chỉ đọc, loopback): http://%s:%s",
            MEMORY_HOST,
            MEMORY_PORT,
        )

    async def cog_unload(self) -> None:
        if self.runner:
            await self.runner.cleanup()
        self.runner = None
        self.site = None

    # -- tiện ích -----------------------------------------------------------

    @staticmethod
    def _error(status: int, message: str) -> web.Response:
        return web.json_response(
            {"error": message},
            status=status,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    def _authorized(self, request: web.Request) -> bool:
        supplied = request.headers.get("X-Peto-Token", "")
        if not supplied or not self.token:
            return False
        # compare_digest để không lộ độ dài khớp qua thời gian so sánh.
        return secrets.compare_digest(supplied, self.token)

    async def _rate_limited(self, request: web.Request) -> bool:
        key = request.remote or "unknown"
        now = time.monotonic()
        async with self.lock:
            bucket = self.requests[key]
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= MAX_REQUESTS_PER_MINUTE:
                return True
            bucket.append(now)
        return False

    @staticmethod
    def _parse_user_id(raw: str) -> int | None:
        if not raw.isdigit() or not (_MIN_ID_LEN <= len(raw) <= _MAX_ID_LEN):
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    # -- route --------------------------------------------------------------

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"status": "ok", "service": "peto-memory-gateway"},
            headers={"Cache-Control": "no-store"},
        )

    async def _memory(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            # Không nói rõ thiếu token hay sai token.
            logger.warning("Memory Gateway từ chối một yêu cầu không hợp lệ.")
            return self._error(401, "unauthorized")

        if await self._rate_limited(request):
            return self._error(429, "rate limited")

        user_id = self._parse_user_id(request.match_info.get("user_id", ""))
        if user_id is None:
            return self._error(400, "user_id không hợp lệ")

        try:
            if await user_memory.is_anonymous_mode(user_id, WEB_SCOPE):
                # Ẩn danh: không đọc trí nhớ dài hạn.
                return web.json_response(
                    {"available": False, "reason": "anonymous", "summary": "", "explicit": []},
                    headers={"Cache-Control": "no-store"},
                )

            summary = await user_memory.get_summary(user_id, WEB_SCOPE)
            explicit = await user_memory.get_explicit_memories(user_id)
        except Exception:
            logger.exception("Đọc trí nhớ thất bại cho user_id=%s", user_id)
            return self._error(500, "không đọc được trí nhớ")

        return web.json_response(
            {
                "available": bool(summary or explicit),
                "summary": summary or "",
                "explicit": list(explicit or []),
            },
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemoryGateway(bot))
