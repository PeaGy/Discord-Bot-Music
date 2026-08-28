"""Persistent per-guild AI policy and fair in-memory request admission."""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
from dotenv import load_dotenv


load_dotenv()


AI_CAPABILITIES = (
    "memory",
    "web",
    "limbus",
    "study",
    "image_read",
    "image_generation",
    "video",
    "danbooru",
    "music",
)
ROLE_CAPABILITIES = ("chat", "web", "limbus", "study", "image", "video", "music")
RESPONSE_MODES = ("mention", "channels", "off")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


GLOBAL_MAX_CONCURRENT = _env_int("AI_SETTINGS_MAX_CONCURRENT", 5, 1, 20)
GLOBAL_MAX_VIDEO_SECONDS = _env_int("XAI_VIDEO_MAX_SECONDS", 120, 5, 3600)
DEFAULT_COOLDOWN_SECONDS = _env_int("AI_SETTINGS_DEFAULT_COOLDOWN_SECONDS", 5, 0, 300)
DEFAULT_HEAVY_COOLDOWN_SECONDS = _env_int(
    "AI_SETTINGS_DEFAULT_HEAVY_COOLDOWN_SECONDS", 30, 0, 900
)
DEFAULT_MAX_CONCURRENT = min(
    GLOBAL_MAX_CONCURRENT,
    _env_int("AI_SETTINGS_DEFAULT_MAX_CONCURRENT", 3, 1, 20),
)
DEFAULT_MAX_VIDEO_SECONDS = min(
    GLOBAL_MAX_VIDEO_SECONDS,
    _env_int("AI_SETTINGS_DEFAULT_MAX_VIDEO_SECONDS", 120, 5, 3600),
)
MAX_QUEUE_PER_GUILD = _env_int("AI_SETTINGS_MAX_QUEUE_PER_GUILD", 6, 0, 50)
QUEUE_TIMEOUT_SECONDS = _env_int("AI_SETTINGS_QUEUE_TIMEOUT_SECONDS", 60, 5, 300)


@dataclass(frozen=True, slots=True)
class GuildAIPolicy:
    guild_id: int
    response_mode: str = "mention"
    memory_enabled: bool = True
    web_enabled: bool = True
    limbus_enabled: bool = True
    study_enabled: bool = True
    image_read_enabled: bool = True
    image_generation_enabled: bool = True
    video_enabled: bool = True
    danbooru_enabled: bool = True
    music_enabled: bool = True
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS
    heavy_cooldown_seconds: int = DEFAULT_HEAVY_COOLDOWN_SECONDS
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    max_video_seconds: int = DEFAULT_MAX_VIDEO_SECONDS
    legacy: bool = False

    def capability_enabled(self, capability: str) -> bool:
        if capability == "chat":
            return self.response_mode != "off"
        return bool(getattr(self, f"{capability}_enabled", False))


class GuildAISettingsStore:
    """SQLite repository for AI policy, channel allowlists and role gates."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("GUILD_SETTINGS_DB", "guild_settings.db")).resolve()

    async def _connect(self) -> aiosqlite.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        return db

    async def init(self) -> None:
        db = await self._connect()
        try:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS guild_ai_settings (
                    guild_id INTEGER PRIMARY KEY,
                    response_mode TEXT NOT NULL DEFAULT 'mention',
                    memory_enabled INTEGER NOT NULL DEFAULT 1,
                    web_enabled INTEGER NOT NULL DEFAULT 1,
                    limbus_enabled INTEGER NOT NULL DEFAULT 1,
                    study_enabled INTEGER NOT NULL DEFAULT 1,
                    image_read_enabled INTEGER NOT NULL DEFAULT 1,
                    image_generation_enabled INTEGER NOT NULL DEFAULT 1,
                    video_enabled INTEGER NOT NULL DEFAULT 1,
                    danbooru_enabled INTEGER NOT NULL DEFAULT 1,
                    music_enabled INTEGER NOT NULL DEFAULT 1,
                    cooldown_seconds INTEGER NOT NULL DEFAULT 5,
                    heavy_cooldown_seconds INTEGER NOT NULL DEFAULT 30,
                    max_concurrent INTEGER NOT NULL DEFAULT 3,
                    max_video_seconds INTEGER NOT NULL DEFAULT 120,
                    legacy INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_by INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS guild_ai_channels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id)
                );
                CREATE TABLE IF NOT EXISTS guild_ai_roles (
                    guild_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    capability TEXT NOT NULL,
                    PRIMARY KEY (guild_id, role_id, capability)
                );
                CREATE TABLE IF NOT EXISTS guild_ai_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row[1])
                for row in await (await db.execute("PRAGMA table_info(guild_ai_settings)"))
                .fetchall()
            }
            if "heavy_cooldown_seconds" not in columns:
                await db.execute(
                    "ALTER TABLE guild_ai_settings ADD COLUMN "
                    "heavy_cooldown_seconds INTEGER NOT NULL DEFAULT 30"
                )
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> GuildAIPolicy:
        return GuildAIPolicy(
            guild_id=int(row["guild_id"]),
            response_mode=str(row["response_mode"]),
            memory_enabled=bool(row["memory_enabled"]),
            web_enabled=bool(row["web_enabled"]),
            limbus_enabled=bool(row["limbus_enabled"]),
            study_enabled=bool(row["study_enabled"]),
            image_read_enabled=bool(row["image_read_enabled"]),
            image_generation_enabled=bool(row["image_generation_enabled"]),
            video_enabled=bool(row["video_enabled"]),
            danbooru_enabled=bool(row["danbooru_enabled"]),
            music_enabled=bool(row["music_enabled"]),
            cooldown_seconds=int(row["cooldown_seconds"]),
            heavy_cooldown_seconds=int(row["heavy_cooldown_seconds"]),
            max_concurrent=int(row["max_concurrent"]),
            max_video_seconds=int(row["max_video_seconds"]),
            legacy=bool(row["legacy"]),
        )

    async def get(self, guild_id: int) -> GuildAIPolicy | None:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM guild_ai_settings WHERE guild_id=?", (int(guild_id),)
            )
            row = await cursor.fetchone()
            return self._from_row(row) if row else None
        finally:
            await db.close()

    async def ensure(self, guild_id: int, *, legacy: bool = False, updated_by: int = 0) -> GuildAIPolicy:
        now = int(time.time())
        db = await self._connect()
        try:
            await db.execute(
                """
                INSERT OR IGNORE INTO guild_ai_settings (
                    guild_id, cooldown_seconds, heavy_cooldown_seconds,
                    max_concurrent, max_video_seconds,
                    legacy, created_at, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(guild_id), DEFAULT_COOLDOWN_SECONDS,
                    DEFAULT_HEAVY_COOLDOWN_SECONDS, DEFAULT_MAX_CONCURRENT,
                    DEFAULT_MAX_VIDEO_SECONDS, int(legacy), now, int(updated_by), now,
                ),
            )
            await db.commit()
        finally:
            await db.close()
        policy = await self.get(guild_id)
        if policy is None:
            raise RuntimeError("Không khởi tạo được cấu hình AI của server")
        return policy

    async def seed_existing_guilds(self, guild_ids: list[int]) -> None:
        """First rollout keeps every guild already containing the bot unchanged."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT value FROM guild_ai_meta WHERE key='existing_guilds_seeded'"
            )
            first_rollout = await cursor.fetchone() is None
        finally:
            await db.close()
        for guild_id in guild_ids:
            if await self.get(guild_id) is None:
                await self.ensure(guild_id, legacy=first_rollout)
        if first_rollout:
            db = await self._connect()
            try:
                await db.execute(
                    "INSERT OR REPLACE INTO guild_ai_meta(key, value) VALUES "
                    "('existing_guilds_seeded', ?)",
                    (str(int(time.time())),),
                )
                await db.commit()
            finally:
                await db.close()

    async def update(self, guild_id: int, updated_by: int, **changes: object) -> GuildAIPolicy:
        allowed = {
            "response_mode", *[f"{name}_enabled" for name in AI_CAPABILITIES],
            "cooldown_seconds", "heavy_cooldown_seconds", "max_concurrent", "max_video_seconds",
        }
        if not changes or any(name not in allowed for name in changes):
            raise ValueError("Cấu hình AI không hợp lệ")
        if "response_mode" in changes and changes["response_mode"] not in RESPONSE_MODES:
            raise ValueError("Chế độ phản hồi không hợp lệ")
        if "cooldown_seconds" in changes:
            changes["cooldown_seconds"] = max(0, min(300, int(changes["cooldown_seconds"])))
        if "heavy_cooldown_seconds" in changes:
            changes["heavy_cooldown_seconds"] = max(
                0, min(900, int(changes["heavy_cooldown_seconds"]))
            )
        if "max_concurrent" in changes:
            changes["max_concurrent"] = max(1, min(GLOBAL_MAX_CONCURRENT, int(changes["max_concurrent"])))
        if "max_video_seconds" in changes:
            changes["max_video_seconds"] = max(5, min(GLOBAL_MAX_VIDEO_SECONDS, int(changes["max_video_seconds"])))
        await self.ensure(guild_id, updated_by=updated_by)
        assignments = ", ".join(f"{name}=?" for name in changes)
        values = [int(value) if isinstance(value, bool) else value for value in changes.values()]
        db = await self._connect()
        try:
            await db.execute(
                f"UPDATE guild_ai_settings SET {assignments}, updated_by=?, updated_at=? "
                "WHERE guild_id=?",
                (*values, int(updated_by), int(time.time()), int(guild_id)),
            )
            await db.commit()
        finally:
            await db.close()
        policy = await self.get(guild_id)
        if policy is None:
            raise RuntimeError("Không đọc lại được cấu hình AI vừa lưu")
        return policy

    async def list_channels(self, guild_id: int) -> set[int]:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT channel_id FROM guild_ai_channels WHERE guild_id=?", (int(guild_id),)
            )
            return {int(row[0]) for row in await cursor.fetchall()}
        finally:
            await db.close()

    async def set_channels(self, guild_id: int, channel_ids: list[int]) -> None:
        db = await self._connect()
        try:
            await db.execute("DELETE FROM guild_ai_channels WHERE guild_id=?", (int(guild_id),))
            await db.executemany(
                "INSERT INTO guild_ai_channels(guild_id, channel_id) VALUES (?, ?)",
                [(int(guild_id), int(channel_id)) for channel_id in dict.fromkeys(channel_ids)],
            )
            await db.commit()
        finally:
            await db.close()

    async def list_roles(self, guild_id: int, capability: str) -> set[int]:
        if capability not in ROLE_CAPABILITIES:
            raise ValueError("Nhóm quyền AI không hợp lệ")
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT role_id FROM guild_ai_roles WHERE guild_id=? AND capability=?",
                (int(guild_id), capability),
            )
            return {int(row[0]) for row in await cursor.fetchall()}
        finally:
            await db.close()

    async def role_map(self, guild_id: int) -> dict[str, set[int]]:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT role_id, capability FROM guild_ai_roles WHERE guild_id=?",
                (int(guild_id),),
            )
            result = {capability: set() for capability in ROLE_CAPABILITIES}
            for row in await cursor.fetchall():
                capability = str(row["capability"])
                if capability in result:
                    result[capability].add(int(row["role_id"]))
            return result
        finally:
            await db.close()

    async def set_roles(self, guild_id: int, capability: str, role_ids: list[int]) -> None:
        if capability not in ROLE_CAPABILITIES:
            raise ValueError("Nhóm quyền AI không hợp lệ")
        db = await self._connect()
        try:
            await db.execute(
                "DELETE FROM guild_ai_roles WHERE guild_id=? AND capability=?",
                (int(guild_id), capability),
            )
            await db.executemany(
                "INSERT INTO guild_ai_roles(guild_id, role_id, capability) VALUES (?, ?, ?)",
                [(int(guild_id), int(role_id), capability) for role_id in dict.fromkeys(role_ids)],
            )
            await db.commit()
        finally:
            await db.close()


class AIAdmissionDenied(RuntimeError):
    def __init__(self, reason: str, retry_after: float = 0.0):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = max(0.0, retry_after)


class _GuildQueue:
    def __init__(self):
        self.active = 0
        self.limit = DEFAULT_MAX_CONCURRENT
        self.waiters: deque[asyncio.Future[None]] = deque()


class AIAdmissionController:
    """Bounded FIFO admission per guild, with one in-flight request per user."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._guilds: dict[int, _GuildQueue] = {}
        self._users: set[int] = set()
        self._last_accepted: dict[int, float] = {}
        self._last_heavy: dict[int, float] = {}

    @asynccontextmanager
    async def admit(
        self,
        guild_id: int,
        user_id: int,
        policy: GuildAIPolicy,
        *,
        heavy: bool = False,
    ):
        waiter: asyncio.Future[None] | None = None
        loop = asyncio.get_running_loop()
        now = loop.time()
        async with self._lock:
            if user_id in self._users:
                raise AIAdmissionDenied("running")
            retry_after = policy.cooldown_seconds - (now - self._last_accepted.get(user_id, -1e9))
            if retry_after > 0:
                raise AIAdmissionDenied("cooldown", retry_after)
            heavy_retry_after = policy.heavy_cooldown_seconds - (
                now - self._last_heavy.get(user_id, -1e9)
            )
            if heavy and heavy_retry_after > 0:
                raise AIAdmissionDenied("heavy_cooldown", heavy_retry_after)
            state = self._guilds.setdefault(int(guild_id), _GuildQueue())
            state.limit = max(1, int(policy.max_concurrent))
            self._wake_waiter(state)
            if state.active < state.limit and not state.waiters:
                state.active += 1
                self._users.add(user_id)
                self._last_accepted[user_id] = now
                if heavy:
                    self._last_heavy[user_id] = now
            else:
                if len(state.waiters) >= MAX_QUEUE_PER_GUILD:
                    raise AIAdmissionDenied("busy")
                waiter = loop.create_future()
                state.waiters.append(waiter)
                self._users.add(user_id)
        if waiter is not None:
            try:
                await asyncio.wait_for(waiter, timeout=QUEUE_TIMEOUT_SECONDS)
                self._last_accepted[user_id] = loop.time()
                if heavy:
                    self._last_heavy[user_id] = loop.time()
            except asyncio.TimeoutError:
                async with self._lock:
                    state = self._guilds.get(int(guild_id))
                    if state is not None:
                        try:
                            state.waiters.remove(waiter)
                        except ValueError:
                            if waiter.done() and not waiter.cancelled():
                                state.active = max(0, state.active - 1)
                                self._wake_waiter(state)
                    self._users.discard(user_id)
                raise AIAdmissionDenied("queue_timeout")
            except asyncio.CancelledError:
                async with self._lock:
                    state = self._guilds.get(int(guild_id))
                    if state is not None:
                        try:
                            state.waiters.remove(waiter)
                        except ValueError:
                            if waiter.done() and not waiter.cancelled():
                                state.active = max(0, state.active - 1)
                                self._wake_waiter(state)
                    self._users.discard(user_id)
                raise
        try:
            yield
        finally:
            async with self._lock:
                state = self._guilds.get(int(guild_id))
                if state is not None:
                    state.active = max(0, state.active - 1)
                    self._wake_waiter(state)
                self._users.discard(user_id)

    @staticmethod
    def _wake_waiter(state: _GuildQueue) -> None:
        while state.active < state.limit and state.waiters:
            waiter = state.waiters.popleft()
            if waiter.cancelled() or waiter.done():
                continue
            state.active += 1
            waiter.set_result(None)


def admission_denial_message(error: AIAdmissionDenied) -> str:
    if error.reason == "cooldown":
        return f"⏳ Chờ khoảng {max(1, int(error.retry_after + 0.999))} giây rồi hỏi Peto tiếp nhé."
    if error.reason == "heavy_cooldown":
        return (
            f"⏳ Tác vụ AI nặng đang cooldown. Chờ khoảng "
            f"{max(1, int(error.retry_after + 0.999))} giây nhé."
        )
    if error.reason == "running":
        return "⏳ Peto vẫn đang xử lý câu trước của bạn, đợi Peto trả lời xong nhé."
    if error.reason == "queue_timeout":
        return "⏳ Hàng chờ AI của server đang lâu quá. Bạn thử lại sau một chút nhé."
    return "⏳ Peto đang xử lý khá nhiều câu trong server này. Bạn thử lại sau nhé."
