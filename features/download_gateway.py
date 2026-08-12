from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from aiohttp import web
from aiohttp.helpers import content_disposition_header
from discord.ext import commands, tasks


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        logger.warning("%s không hợp lệ; dùng mặc định %s", name, default)
        return default


DOWNLOAD_HOST = os.getenv("DOWNLOAD_GATEWAY_HOST", "127.0.0.1").strip()
DOWNLOAD_PORT = _env_int("DOWNLOAD_GATEWAY_PORT", 8765, 1)
PUBLIC_BASE_URL = os.getenv(
    "DOWNLOAD_PUBLIC_BASE_URL",
    "",
).strip().rstrip("/")
FILE_TTL_SECONDS = _env_int("DOWNLOAD_FILE_TTL_SECONDS", 2 * 60 * 60, 60)
MAX_FILE_BYTES = _env_int("DOWNLOAD_MAX_FILE_MIB", 512, 10) * 1024 * 1024
MAX_TOTAL_BYTES = _env_int("DOWNLOAD_MAX_TOTAL_MIB", 2048, 10) * 1024 * 1024
MAX_REQUESTS_PER_TOKEN = _env_int("DOWNLOAD_MAX_REQUESTS_PER_TOKEN", 20, 1)
MAX_REQUESTS_PER_IP_MINUTE = _env_int("DOWNLOAD_MAX_REQUESTS_PER_IP_MINUTE", 30, 1)
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_STORAGE_DIR", "temp_downloads")).resolve()


class DownloadGatewayError(RuntimeError):
    """Lỗi có thể hiển thị cho người dùng khi xuất bản file thất bại."""


@dataclass
class DownloadEntry:
    token: str
    path: Path
    display_name: str
    owner_id: int
    created_at: float
    expires_at: float
    request_count: int = 0


class DownloadGateway(commands.Cog):
    """File gateway cục bộ, được công khai duy nhất qua Cloudflare Tunnel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.public_base_url = PUBLIC_BASE_URL
        self.max_file_bytes = MAX_FILE_BYTES
        self.enabled = self._valid_public_url(self.public_base_url)
        self.entries: dict[str, DownloadEntry] = {}
        self.ip_requests: dict[str, deque[float]] = defaultdict(deque)
        self.lock = asyncio.Lock()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    @staticmethod
    def _valid_public_url(value: str) -> bool:
        try:
            parsed = urlparse(value)
        except ValueError:
            return False
        return parsed.scheme == "https" and bool(parsed.hostname)

    async def cog_load(self) -> None:
        if not self.enabled:
            logger.warning(
                "Download Gateway bị tắt vì DOWNLOAD_PUBLIC_BASE_URL chưa phải HTTPS hợp lệ."
            )
            return

        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        app = web.Application(client_max_size=1024)
        app.router.add_get("/health", self._health)
        app.router.add_get("/d/{token}", self._download)
        self.runner = web.AppRunner(
            app,
            access_log=None,
            handle_signals=False,
        )
        try:
            await self.runner.setup()
            self.site = web.TCPSite(
                self.runner,
                host=DOWNLOAD_HOST,
                port=DOWNLOAD_PORT,
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
                "Không thể mở Download Gateway tại http://%s:%s",
                DOWNLOAD_HOST,
                DOWNLOAD_PORT,
            )
            return

        self.cleanup_files.start()
        logger.info(
            "Download Gateway sẵn sàng: http://%s:%s -> %s "
            "(file tối đa %.0f MiB, TTL %s phút)",
            DOWNLOAD_HOST,
            DOWNLOAD_PORT,
            self.public_base_url,
            self.max_file_bytes / (1024 * 1024),
            FILE_TTL_SECONDS // 60,
        )

    async def cog_unload(self) -> None:
        self.cleanup_files.cancel()
        if self.runner:
            await self.runner.cleanup()
        self.runner = None
        self.site = None

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"status": "ok", "service": "peto-download-gateway"},
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @staticmethod
    def _visitor_ip(request: web.Request) -> str:
        forwarded = request.headers.get("CF-Connecting-IP", "").strip()
        return forwarded or request.remote or "unknown"

    def _rate_limited(self, request: web.Request) -> bool:
        now = time.monotonic()
        history = self.ip_requests[self._visitor_ip(request)]
        while history and now - history[0] > 60:
            history.popleft()
        if len(history) >= MAX_REQUESTS_PER_IP_MINUTE:
            return True
        history.append(now)
        return False

    @staticmethod
    def _error(status: int, message: str) -> web.Response:
        return web.Response(
            status=status,
            text=message,
            content_type="text/plain",
            charset="utf-8",
            headers={
                "Cache-Control": "no-store, private",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    async def _download(self, request: web.Request) -> web.StreamResponse:
        if self._rate_limited(request):
            return self._error(429, "Bạn gửi quá nhiều yêu cầu. Hãy thử lại sau một phút.")

        token = request.match_info.get("token", "")
        if not token or len(token) > 128:
            return self._error(404, "Link tải không tồn tại hoặc đã hết hạn.")

        async with self.lock:
            entry = self.entries.get(token)
            now = time.time()
            if (
                entry is None
                or now >= entry.expires_at
                or not entry.path.is_file()
            ):
                return self._error(404, "Link tải không tồn tại hoặc đã hết hạn.")
            if entry.request_count >= MAX_REQUESTS_PER_TOKEN:
                return self._error(410, "Link tải đã đạt giới hạn truy cập.")
            entry.request_count += 1

        response = web.FileResponse(
            entry.path,
            headers={
                "Cache-Control": "no-store, private, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Disposition": content_disposition_header(
                    "attachment",
                    filename=entry.display_name,
                ),
            },
        )
        return response

    async def _current_storage_bytes(self) -> int:
        def calculate() -> int:
            total = 0
            if not DOWNLOAD_DIR.exists():
                return total
            for path in DOWNLOAD_DIR.iterdir():
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
            return total

        return await asyncio.to_thread(calculate)

    async def publish_file(
        self,
        source_path: str,
        *,
        display_name: str,
        owner_id: int,
    ) -> str:
        """Chuyển file vào gateway và trả bearer URL có token không đoán được."""
        if not self.enabled:
            raise DownloadGatewayError("Download Gateway chưa được bật.")
        source = Path(source_path).resolve()
        if not source.is_file():
            raise DownloadGatewayError("Không tìm thấy file cần công khai.")
        size = source.stat().st_size
        if size <= 0:
            raise DownloadGatewayError("File tải về bị rỗng.")
        if size > self.max_file_bytes:
            raise DownloadGatewayError(
                f"File vượt giới hạn gateway {self.max_file_bytes / (1024 * 1024):.0f} MiB."
            )

        token = secrets.token_urlsafe(32)
        extension = source.suffix.casefold()
        if not re_safe_extension(extension):
            extension = ".bin"
        target = DOWNLOAD_DIR / f"{token}{extension}"

        async with self.lock:
            current_size = await self._current_storage_bytes()
            if current_size + size > MAX_TOTAL_BYTES:
                raise DownloadGatewayError(
                    "Kho file tạm đang đầy; hãy đợi các link cũ hết hạn rồi thử lại."
                )
            try:
                await asyncio.to_thread(shutil.move, str(source), str(target))
            except OSError as error:
                raise DownloadGatewayError("Không thể chuyển file vào kho tải tạm.") from error
            now = time.time()
            self.entries[token] = DownloadEntry(
                token=token,
                path=target,
                display_name=sanitize_download_name(display_name),
                owner_id=int(owner_id),
                created_at=now,
                expires_at=now + FILE_TTL_SECONDS,
            )

        return f"{self.public_base_url}/d/{quote(token, safe='')}"

    @tasks.loop(minutes=10)
    async def cleanup_files(self) -> None:
        now = time.time()
        async with self.lock:
            expired = [
                (token, entry.path)
                for token, entry in self.entries.items()
                if now >= entry.expires_at
            ]
            for token, path in expired:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Chưa thể xóa file gateway đang bận: %s", path.name)
                    continue
                self.entries.pop(token, None)

            # Dọn file mồ côi sau restart. Cho thêm 15 phút để tránh đụng một
            # response đang hoàn tất đúng thời điểm token hết hạn.
            orphan_cutoff = now - FILE_TTL_SECONDS - 15 * 60
            known_paths = {entry.path for entry in self.entries.values()}
            for path in DOWNLOAD_DIR.iterdir():
                try:
                    if (
                        path.is_file()
                        and path not in known_paths
                        and path.stat().st_mtime < orphan_cutoff
                    ):
                        path.unlink()
                except OSError:
                    logger.debug("Bỏ qua file gateway chưa thể dọn: %s", path)

    @cleanup_files.before_loop
    async def before_cleanup_files(self) -> None:
        await self.bot.wait_until_ready()


def re_safe_extension(extension: str) -> bool:
    return bool(extension and len(extension) <= 10 and extension[1:].isalnum())


def sanitize_download_name(value: str) -> str:
    cleaned = "".join(
        "_" if char in '\\/:*?\"<>|\r\n\x00' or ord(char) < 32 else char
        for char in str(value or "download.bin")
    )
    cleaned = " ".join(cleaned.split()).strip(" ._")
    return (cleaned or "download.bin")[:140]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DownloadGateway(bot))
