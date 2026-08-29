"""Thông báo Daily Reset đa game, có đăng ký DM và chống gửi trùng."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

from guild_settings import (
    GuildNotification,
    GuildSettingsStore,
    notification_destinations,
)


logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("%s không hợp lệ; dùng mặc định %s", name, default)
        return default
    return min(maximum, max(minimum, value))


def _optional_snowflake(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s phải là Discord ID dạng số; bỏ qua", name)
        return None
    return value if value > 0 else None


def parse_utc_time(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
        return hour, minute
    except (AttributeError, TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class DailyGame:
    slug: str
    env_prefix: str
    name: str
    audience: str
    reset_hour: int
    reset_minute: int
    color: int
    emoji: str
    footer: str
    checklist: tuple[tuple[str, str], ...]
    # Empty means every UTC date. Limbus uses Wednesday UTC because its
    # Thursday 06:00 KST reset occurs at Wednesday 21:00 UTC.
    reset_weekdays_utc: tuple[int, ...] = ()
    schedule_label: str = "hằng ngày"
    channel_id: int | None = None
    role_id: int | None = None
    image_url: str = ""


BASE_GAMES: tuple[DailyGame, ...] = (
    DailyGame(
        slug="nikke",
        env_prefix="NIKKE",
        name="GODDESS OF VICTORY: NIKKE",
        audience="Commander",
        reset_hour=20,
        reset_minute=0,
        color=0x3498DB,
        emoji="🔫",
        footer="Giữ an toàn trên mặt đất, Commander!",
        checklist=(
            ("Gói miễn phí", "Nhận gói miễn phí hằng ngày trong Cash Shop."),
            ("Advise", "Trò chuyện/Advise Nikkes và nhận Bond."),
            ("Bạn bè", "Gửi Social Points cho bạn bè."),
            ("Interception", "Hoàn thành Anomaly hoặc Special Interception."),
            ("Simulation Room", "Hoàn thành Simulation Room và Overclock nếu đang mở."),
            ("Bulletin Board", "Gửi đủ Dispatch và nhận phần thưởng."),
            ("Outpost", "Nhận Outpost Defense và dùng Wipe Out khi cần."),
        ),
    ),
    DailyGame(
        slug="blue_archive",
        env_prefix="BLUE_ARCHIVE",
        name="Blue Archive",
        audience="Sensei",
        reset_hour=19,
        reset_minute=0,
        color=0x65B9E8,
        emoji="🏫",
        footer="Đừng để AP đầy nhé, Sensei!",
        checklist=(
            ("AP", "Tiêu AP và nhận AP trong Cafe/đăng nhập."),
            ("Cafe", "Thu lợi nhuận, mời và tương tác với học sinh."),
            ("Bounty & Lessons", "Dùng lượt Bounty, Lesson và Scrimmage cần thiết."),
            ("Hard Mode", "Dùng lượt Hard Mode cho shard đang cần."),
            ("Shop", "Kiểm tra Normal Shop và Tactical Challenge Shop."),
            ("Nhiệm vụ", "Nhận phần thưởng Daily Missions trước reset."),
        ),
    ),
    DailyGame(
        slug="trickcal",
        env_prefix="TRICKCAL",
        name="Trickcal: Chibi Go",
        audience="Giáo chủ",
        reset_hour=19,
        reset_minute=0,
        color=0x90EE90,
        emoji="🍬",
        footer="Cầu Yggdrasil phù hộ cho Giáo chủ!",
        checklist=(
            ("Đăng nhập", "Nhận quà đăng nhập và các phần thưởng miễn phí."),
            ("Candy", "Dùng Candy/Star Candy, tránh để tài nguyên đầy."),
            ("Daily Schedule", "Hoàn thành lịch trình và nhiệm vụ hằng ngày."),
            ("Apostles", "Dùng Banquet Hall và tăng bond cho Apostles."),
            ("Cửa hàng", "Kiểm tra vật phẩm miễn phí và vật phẩm reset ngày."),
            ("Pet & Relic", "Nhận quà Pet và tài nguyên Yggdrasil Relic."),
        ),
    ),
    DailyGame(
        slug="chaos_zero_nightmare",
        env_prefix="CHAOS_ZERO_NIGHTMARE",
        name="Chaos Zero Nightmare",
        audience="Protos",
        reset_hour=18,
        reset_minute=0,
        color=0x9B30FF,
        emoji="🌌",
        footer="Luôn cảnh giác, Protos. Nightmare không bao giờ ngủ.",
        checklist=(
            ("Aether", "Nhận và tiêu Aether cho Simulation cần farm."),
            ("Daily Order", "Hoàn thành Daily Order và nhận toàn bộ mốc thưởng."),
            ("Partner", "Dùng Communication Pass và tăng Affinity."),
            ("Policy Office", "Kiểm tra các Policy đang chờ xử lý."),
            ("Diner", "Nhận hoặc dùng quyền lợi tại Starshine Diner."),
            ("Shop & Mail", "Kiểm tra cửa hàng, quà đăng nhập và hộp thư."),
        ),
    ),
    DailyGame(
        slug="limbus_company",
        env_prefix="LIMBUS_COMPANY",
        name="Limbus Company",
        audience="Manager",
        # 06:00 KST (UTC+9) tương ứng 21:00 UTC của ngày hôm trước.
        reset_hour=21,
        reset_minute=0,
        color=0xB21E35,
        emoji="⏰",
        footer="Face the Sin, Save the E.G.O, Manager.",
        checklist=(
            (
                "Mirror Dungeon",
                "Weekly Bonus đã được làm mới — hoàn thành Mirror Dungeon để "
                "nhận Lunacy và Battle Pass EXP của tuần này.",
            ),
            (
                "Weekly Missions",
                "Kiểm tra và hoàn thành các nhiệm vụ tuần trong Limbus Pass.",
            ),
        ),
        reset_weekdays_utc=(2,),
        schedule_label="Thứ Năm hằng tuần (06:00 KST)",
    ),
    DailyGame(
        slug="brown_dust_2",
        env_prefix="BROWN_DUST_2",
        name="Brown Dust 2",
        audience="Master",
        reset_hour=0,
        reset_minute=0,
        color=0x8B4513,
        emoji="⚔️",
        footer="Đừng quên lượt quay miễn phí, Master!",
        checklist=(
            ("Free Draw", "Dùng lượt quay miễn phí trên các banner đang mở."),
            ("Rice & Torch", "Tiêu Cooked Rice và Torch, tránh để tràn."),
            ("Daily Missions", "Hoàn thành nhiệm vụ và nhận Dia/tài nguyên."),
            ("Event", "Dùng lượt hoặc stamina của event đang diễn ra."),
            ("Mirror Wars", "Dùng lượt PVP cần thiết trước reset."),
            ("Bulletin Board", "Hoàn thành quest hằng ngày và nhận thưởng."),
            ("Shop & Pub", "Kiểm tra vật phẩm reset ngày, Mail và Pub."),
        ),
    ),
)


def load_games_from_env(*, include_all: bool = False) -> dict[str, DailyGame]:
    requested = {
        value.strip().casefold().replace("-", "_")
        for value in os.getenv(
            "DAILY_RESET_GAMES",
            "nikke,blue_archive,trickcal,chaos_zero_nightmare,limbus_company,brown_dust_2",
        ).split(",")
        if value.strip()
    }
    aliases = {
        "limbus": "limbus_company",
        "ba": "blue_archive",
        "bd2": "brown_dust_2",
        "czn": "chaos_zero_nightmare",
    }
    requested = {aliases.get(value, value) for value in requested}
    if include_all or "all" in requested:
        requested = {game.slug for game in BASE_GAMES}
    games: dict[str, DailyGame] = {}
    for base in BASE_GAMES:
        if base.slug not in requested:
            continue
        prefix = f"DAILY_RESET_{base.env_prefix}"
        hour, minute = parse_utc_time(
            os.getenv(f"{prefix}_TIME", f"{base.reset_hour:02d}:{base.reset_minute:02d}"),
            (base.reset_hour, base.reset_minute),
        )
        games[base.slug] = replace(
            base,
            reset_hour=hour,
            reset_minute=minute,
            channel_id=_optional_snowflake(f"{prefix}_CHANNEL_ID"),
            role_id=_optional_snowflake(f"{prefix}_ROLE_ID"),
            image_url=os.getenv(f"{prefix}_IMAGE_URL", "").strip(),
        )
    return games


@dataclass(frozen=True, slots=True)
class DailyEvent:
    game_slug: str
    event_type: str
    scheduled_at: datetime
    reset_at: datetime

    @property
    def key(self) -> str:
        return (
            f"{self.game_slug}:{self.event_type}:"
            f"{self.scheduled_at.astimezone(UTC).strftime('%Y%m%dT%H%M')}"
        )


def reset_at_for_date(game: DailyGame, date_value) -> datetime:
    return datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        game.reset_hour,
        game.reset_minute,
        tzinfo=UTC,
    )


def is_reset_date(game: DailyGame, date_value) -> bool:
    return (
        not game.reset_weekdays_utc
        or date_value.weekday() in game.reset_weekdays_utc
    )


def next_reset_at(game: DailyGame, now: datetime) -> datetime:
    now = now.astimezone(UTC)
    for day_offset in range(8):
        date_value = (now + timedelta(days=day_offset)).date()
        if not is_reset_date(game, date_value):
            continue
        candidate = reset_at_for_date(game, date_value)
        if candidate > now:
            return candidate
    raise RuntimeError(f"Không tính được lần reset tiếp theo của {game.slug}")


def due_events(
    game: DailyGame,
    now: datetime,
    *,
    warning_minutes: int,
    catchup_minutes: int,
) -> list[DailyEvent]:
    now = now.astimezone(UTC)
    due: list[DailyEvent] = []
    for day_offset in (-1, 0, 1):
        date_value = (now + timedelta(days=day_offset)).date()
        if not is_reset_date(game, date_value):
            continue
        reset_at = reset_at_for_date(game, date_value)
        candidates = [("reset", reset_at)]
        if warning_minutes > 0:
            candidates.append(
                ("warning", reset_at - timedelta(minutes=warning_minutes))
            )
        for event_type, scheduled_at in candidates:
            if scheduled_at <= now < scheduled_at + timedelta(minutes=catchup_minutes):
                due.append(
                    DailyEvent(
                        game_slug=game.slug,
                        event_type=event_type,
                        scheduled_at=scheduled_at,
                        reset_at=reset_at,
                    )
                )
    return sorted(due, key=lambda event: event.scheduled_at)


def build_daily_embed(
    game: DailyGame,
    event: DailyEvent,
    *,
    preview: bool = False,
) -> discord.Embed:
    reset_timestamp = int(event.reset_at.timestamp())
    is_limbus_weekly = game.slug == "limbus_company" and bool(
        game.reset_weekdays_utc
    )
    if event.event_type == "warning" and is_limbus_weekly:
        title = "⏰ Mirror Dungeon sắp reset tuần"
        description = (
            f"Manager, Mirror Dungeon sẽ reset <t:{reset_timestamp}:R> "
            f"(<t:{reset_timestamp}:t>). Hãy dùng Weekly Bonus còn lại nếu chưa nhận."
        )
    elif event.event_type == "warning":
        title = f"⏰ {game.name} sắp reset"
        description = (
            f"{game.audience}, server sẽ reset <t:{reset_timestamp}:R> "
            f"(<t:{reset_timestamp}:t>). Hãy kiểm tra các việc còn thiếu."
        )
    elif is_limbus_weekly:
        title = "⏰ Mirror Dungeon đã reset tuần!"
        description = (
            "Chào Manager! Weekly Bonus của Mirror Dungeon đã được làm mới. "
            "Đừng quên nguồn Lunacy quan trọng của tuần này nhé."
        )
    else:
        title = f"{game.emoji} {game.name} đã reset!"
        description = (
            f"Chào {game.audience}! Một ngày mới đã bắt đầu. "
            "Đây là checklist gợi ý; hãy ưu tiên event đang diễn ra trong game."
        )

    embed = discord.Embed(
        title=("🧪 " if preview else "") + title,
        description=description,
        color=game.color,
        timestamp=event.scheduled_at,
    )
    if event.event_type == "reset":
        for name, value in game.checklist:
            embed.add_field(name=name, value=value, inline=False)
    if game.image_url:
        embed.set_image(url=game.image_url)
    reset_kind = "Weekly Reset" if game.reset_weekdays_utc else "Daily Reset"
    footer = f"Peto {reset_kind} • {game.schedule_label} • {game.footer}"
    if preview:
        footer += " • Bản kiểm thử, không lưu trạng thái"
    embed.set_footer(text=footer[:2048])
    return embed


class DailyResetSubscriptionView(discord.ui.View):
    def __init__(self, cog: "DailyReset", game_slug: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.game_slug = game_slug

        subscribe = discord.ui.Button(
            label="Nhận qua DM",
            emoji="✉️",
            style=discord.ButtonStyle.primary,
            custom_id=f"peto:dailyreset:subscribe:{game_slug}",
        )
        unsubscribe = discord.ui.Button(
            label="Tắt DM",
            emoji="🔕",
            style=discord.ButtonStyle.secondary,
            custom_id=f"peto:dailyreset:unsubscribe:{game_slug}",
        )
        subscribe.callback = self._subscribe
        unsubscribe.callback = self._unsubscribe
        self.add_item(subscribe)
        self.add_item(unsubscribe)

    async def _subscribe(self, interaction: discord.Interaction) -> None:
        await self.cog.set_subscription(interaction.user.id, self.game_slug, True)
        game = self.cog.games[self.game_slug]
        await interaction.response.send_message(
            f"✉️ Bạn sẽ nhận nhắc {game.schedule_label} của **{game.name}** qua DM.",
            ephemeral=True,
        )

    async def _unsubscribe(self, interaction: discord.Interaction) -> None:
        await self.cog.set_subscription(interaction.user.id, self.game_slug, False)
        game = self.cog.games[self.game_slug]
        await interaction.response.send_message(
            f"🔕 Đã tắt nhắc {game.schedule_label} của **{game.name}** qua DM.",
            ephemeral=True,
        )


class DailyReset(commands.Cog):
    dailyreset = app_commands.Group(
        name="dailyreset",
        description="Lịch reset và nhắc việc của game",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.settings = GuildSettingsStore()
        self.enabled = _env_bool("DAILY_RESET_ENABLED", False)
        self.legacy_games = load_games_from_env()
        # Public guild settings may enable any supported game. DAILY_RESET_GAMES
        # continues to control only the old .env fallback destinations.
        self.games = load_games_from_env(include_all=True)
        self.warning_minutes = _env_int(
            "DAILY_RESET_WARNING_MINUTES", 60, 0, 1440
        )
        self.catchup_minutes = _env_int(
            "DAILY_RESET_CATCHUP_MINUTES", 5, 1, 60
        )
        self.db_path = Path(
            os.getenv("DAILY_RESET_DB", "daily_reset_notifications.db")
        )
        self.poll_lock = asyncio.Lock()
        self.last_success_at: float | None = None
        self.last_error: str | None = None
        self.views = {
            slug: DailyResetSubscriptionView(self, slug)
            for slug in self.games
        }

    async def cog_load(self) -> None:
        await self._init_db()
        await self.settings.init()
        for view in self.views.values():
            self.bot.add_view(view)
        if not self.enabled:
            logger.info(
                "Daily Reset đang tắt; dùng DAILY_RESET_ENABLED=true để bật"
            )
            return
        self.daily_reset_loop.start()
        logger.info(
            "Daily Reset sẵn sàng cho %s game: %s",
            len(self.games),
            ", ".join(game.name for game in self.games.values()),
        )

    async def cog_unload(self) -> None:
        self.daily_reset_loop.cancel()

    async def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_reset_sent (
                    event_key TEXT PRIMARY KEY,
                    game_slug TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    sent_at INTEGER NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_reset_subscriptions (
                    user_id INTEGER NOT NULL,
                    game_slug TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, game_slug)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_reset_guild_sent (
                    guild_id INTEGER NOT NULL,
                    event_key TEXT NOT NULL,
                    game_slug TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    sent_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, event_key)
                )
                """
            )
            await db.commit()

    async def set_subscription(
        self,
        user_id: int,
        game_slug: str,
        enabled: bool,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            if enabled:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO daily_reset_subscriptions
                    (user_id, game_slug, created_at) VALUES (?, ?, ?)
                    """,
                    (int(user_id), game_slug, int(time.time())),
                )
            else:
                await db.execute(
                    """
                    DELETE FROM daily_reset_subscriptions
                    WHERE user_id = ? AND game_slug = ?
                    """,
                    (int(user_id), game_slug),
                )
            await db.commit()

    async def _subscribers(self, game_slug: str) -> list[int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT user_id FROM daily_reset_subscriptions
                WHERE game_slug = ? ORDER BY created_at
                """,
                (game_slug,),
            )
            return [int(row[0]) for row in await cursor.fetchall()]

    async def _is_sent(self, guild_id: int, event_key: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM daily_reset_guild_sent "
                "WHERE guild_id = ? AND event_key = ?",
                (int(guild_id), event_key),
            )
            return await cursor.fetchone() is not None

    async def _mark_sent(self, guild_id: int, event: DailyEvent) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO daily_reset_guild_sent
                (guild_id, event_key, game_slug, event_type, scheduled_at, sent_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(guild_id),
                    event.key,
                    event.game_slug,
                    event.event_type,
                    event.scheduled_at.isoformat(),
                    int(time.time()),
                ),
            )
            await db.execute(
                "DELETE FROM daily_reset_guild_sent WHERE sent_at < ?",
                (int(time.time()) - 120 * 86400,),
            )
            await db.commit()

    async def _resolve_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        if not hasattr(channel, "send"):
            raise RuntimeError("Discord ID đích không phải kênh gửi tin nhắn")
        return channel

    async def _destinations(self, game: DailyGame) -> list[GuildNotification]:
        legacy = self.legacy_games.get(game.slug)
        return await notification_destinations(
            self.bot,
            self.settings,
            "daily_reset",
            game.slug,
            legacy_channel_id=legacy.channel_id if legacy else None,
            legacy_role_id=legacy.role_id if legacy else None,
        )

    async def _send_subscriber_dms(
        self,
        game: DailyGame,
        event: DailyEvent,
    ) -> tuple[int, int]:
        user_ids = await self._subscribers(game.slug)
        if not user_ids:
            return 0, 0
        semaphore = asyncio.Semaphore(5)

        async def send_one(user_id: int) -> bool:
            async with semaphore:
                try:
                    user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                    await user.send(embed=build_daily_embed(game, event))
                    return True
                except (discord.Forbidden, discord.NotFound):
                    return False
                except discord.HTTPException as error:
                    logger.warning(
                        "Không gửi được Daily Reset DM tới user=%s: %s",
                        user_id,
                        error,
                    )
                    return False
                except Exception as error:
                    logger.warning(
                        "Lỗi bất ngờ khi gửi Daily Reset DM tới user=%s: %s",
                        user_id,
                        error,
                    )
                    return False

        results = await asyncio.gather(*(send_one(user_id) for user_id in user_ids))
        return sum(results), len(results) - sum(results)

    async def _announce_event(
        self,
        game: DailyGame,
        event: DailyEvent,
        *,
        destination: GuildNotification | None = None,
        test: bool = False,
    ):
        if destination is None:
            destinations = await self._destinations(game)
            destination = destinations[0] if destinations else None
        if destination is None or destination.channel_id is None:
            raise RuntimeError(f"{game.name} chưa có kênh thông báo")
        channel = await self._resolve_channel(destination.channel_id)
        role_content = None
        if not test and event.event_type == "reset" and destination.role_id:
            role_content = f"<@&{destination.role_id}>"
        message = await channel.send(
            content=role_content,
            embed=build_daily_embed(game, event, preview=test),
            view=None if test or event.event_type == "warning" else self.views[game.slug],
            allowed_mentions=(
                discord.AllowedMentions.none()
                if not role_content
                else discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=True,
                )
            ),
        )
        return message

    async def check_due_events(self, now: datetime | None = None) -> int:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        sent_count = 0
        async with self.poll_lock:
            for game in self.games.values():
                destinations = await self._destinations(game)
                if not destinations:
                    continue
                for event in due_events(
                    game,
                    now,
                    warning_minutes=self.warning_minutes,
                    catchup_minutes=self.catchup_minutes,
                ):
                    for destination in destinations:
                        if await self._is_sent(destination.guild_id, event.key):
                            continue
                        try:
                            await self._announce_event(
                                game,
                                event,
                                destination=destination,
                            )
                        except Exception as error:
                            logger.warning(
                                "Không gửi được Daily Reset %s tới guild=%s "
                                "channel=%s: %s",
                                game.slug,
                                destination.guild_id,
                                destination.channel_id,
                                error,
                            )
                            continue
                        await self._mark_sent(destination.guild_id, event)
                        sent_count += 1

                    # Đăng ký DM đi theo user_id, không nhân lên theo số server.
                    if not await self._is_sent(0, event.key):
                        delivered, failed = await self._send_subscriber_dms(game, event)
                        await self._mark_sent(0, event)
                        if delivered or failed:
                            logger.info(
                                "Daily Reset %s DM %s: thành công=%s, lỗi/đóng DM=%s",
                                game.slug,
                                event.event_type,
                                delivered,
                                failed,
                            )
            self.last_success_at = time.time()
            self.last_error = None
        return sent_count

    @tasks.loop(seconds=30)
    async def daily_reset_loop(self) -> None:
        try:
            count = await self.check_due_events()
            if count:
                logger.info("Đã gửi %s thông báo Daily Reset", count)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.last_error = str(error)[:500]
            logger.exception("Daily Reset scheduler gặp lỗi: %s", error)

    @daily_reset_loop.before_loop
    async def before_daily_reset_loop(self) -> None:
        await self.bot.wait_until_ready()
        for game in self.legacy_games.values():
            await self.settings.migrate_legacy(
                self.bot,
                "daily_reset",
                game.slug,
                game.channel_id,
                game.role_id,
            )

    def _game(self, value: str) -> DailyGame | None:
        normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized in self.games:
            return self.games[normalized]
        for game in self.games.values():
            if normalized == game.name.casefold().replace(" ", "_"):
                return game
        return None

    async def game_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        del interaction
        needle = current.casefold().strip()
        return [
            app_commands.Choice(name=game.name[:100], value=game.slug)
            for game in self.games.values()
            if not needle or needle in game.name.casefold() or needle in game.slug
        ][:25]

    async def _require_owner(self, interaction: discord.Interaction) -> bool:
        if await self.bot.is_owner(interaction.user):
            return True
        await interaction.response.send_message(
            "❌ Chỉ chủ bot mới được dùng thao tác quản trị Daily Reset.",
            ephemeral=True,
        )
        return False

    @dailyreset.command(name="next", description="Xem lần reset tiếp theo của một game")
    @app_commands.describe(game="Tên game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def daily_next(self, interaction: discord.Interaction, game: str) -> None:
        selected = self._game(game)
        if selected is None:
            return await interaction.response.send_message(
                "❌ Game này chưa nằm trong Daily Reset của Peto.", ephemeral=True
            )
        upcoming = next_reset_at(selected, datetime.now(UTC))
        timestamp = int(upcoming.timestamp())
        await interaction.response.send_message(
            f"{selected.emoji} **{selected.name}** reset <t:{timestamp}:R> "
            f"— <t:{timestamp}:F>.",
            ephemeral=True,
        )

    @dailyreset.command(name="subscribe", description="Nhận Daily Reset của một game qua DM")
    @app_commands.describe(game="Tên game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def daily_subscribe(self, interaction: discord.Interaction, game: str) -> None:
        selected = self._game(game)
        if selected is None:
            return await interaction.response.send_message(
                "❌ Game này chưa nằm trong Daily Reset của Peto.", ephemeral=True
            )
        await self.set_subscription(interaction.user.id, selected.slug, True)
        await interaction.response.send_message(
            f"✉️ Bạn sẽ nhận nhắc {selected.schedule_label} của "
            f"**{selected.name}** qua DM.",
            ephemeral=True,
        )

    @dailyreset.command(name="unsubscribe", description="Tắt Daily Reset DM của một game")
    @app_commands.describe(game="Tên game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def daily_unsubscribe(self, interaction: discord.Interaction, game: str) -> None:
        selected = self._game(game)
        if selected is None:
            return await interaction.response.send_message(
                "❌ Game này chưa nằm trong Daily Reset của Peto.", ephemeral=True
            )
        await self.set_subscription(interaction.user.id, selected.slug, False)
        await interaction.response.send_message(
            f"🔕 Đã tắt nhắc {selected.schedule_label} của "
            f"**{selected.name}** qua DM.",
            ephemeral=True,
        )

    @dailyreset.command(name="subscriptions", description="Xem các Daily Reset DM đã đăng ký")
    async def daily_subscriptions(self, interaction: discord.Interaction) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT game_slug FROM daily_reset_subscriptions
                WHERE user_id = ? ORDER BY created_at
                """,
                (interaction.user.id,),
            )
            slugs = [str(row[0]) for row in await cursor.fetchall()]
        names = [self.games[slug].name for slug in slugs if slug in self.games]
        text = "\n".join(f"• {name}" for name in names) or "Bạn chưa đăng ký game nào."
        await interaction.response.send_message(
            f"**Daily Reset đang nhận qua DM**\n{text}", ephemeral=True
        )

    @dailyreset.command(name="status", description="[Chủ bot] Xem cấu hình Daily Reset")
    async def daily_status(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        now = datetime.now(UTC)
        rows = []
        for game in self.games.values():
            upcoming = next_reset_at(game, now)
            destinations = await self._destinations(game)
            destination = f"{len(destinations)} server"
            rows.append(
                f"• **{game.name}** — {game.schedule_label}, "
                f"lần tới <t:{int(upcoming.timestamp())}:F>, {destination}"
            )
        last_check = (
            f"<t:{int(self.last_success_at)}:R>" if self.last_success_at else "chưa có"
        )
        message = (
            f"**Daily Reset: {'Đang bật' if self.enabled else 'Đang tắt'}**\n"
            f"Cảnh báo trước: `{self.warning_minutes}` phút • "
            f"bù sau restart: `{self.catchup_minutes}` phút\n"
            + "\n".join(rows)
            + f"\nLần kiểm tra thành công: {last_check}"
        )
        if self.last_error:
            message += f"\nLỗi gần nhất: `{self.last_error[:250]}`"
        await interaction.response.send_message(message, ephemeral=True)

    @dailyreset.command(name="preview", description="[Chủ bot] Xem thử card Daily Reset")
    @app_commands.describe(game="Tên game", event="Loại thông báo")
    @app_commands.choices(
        event=[
            app_commands.Choice(name="Reset", value="reset"),
            app_commands.Choice(name="Cảnh báo trước", value="warning"),
        ]
    )
    @app_commands.autocomplete(game=game_autocomplete)
    async def daily_preview(
        self,
        interaction: discord.Interaction,
        game: str,
        event: app_commands.Choice[str],
    ) -> None:
        if not await self._require_owner(interaction):
            return
        selected = self._game(game)
        if selected is None:
            return await interaction.response.send_message("❌ Không tìm thấy game.", ephemeral=True)
        reset_at = next_reset_at(selected, datetime.now(UTC))
        scheduled = (
            reset_at - timedelta(minutes=self.warning_minutes)
            if event.value == "warning"
            else reset_at
        )
        item = DailyEvent(selected.slug, event.value, scheduled, reset_at)
        await interaction.response.send_message(
            embed=build_daily_embed(selected, item, preview=True), ephemeral=True
        )

    @dailyreset.command(name="test", description="[Chủ bot] Gửi thử card vào kênh game")
    @app_commands.describe(game="Tên game", event="Loại thông báo")
    @app_commands.choices(
        event=[
            app_commands.Choice(name="Reset", value="reset"),
            app_commands.Choice(name="Cảnh báo trước", value="warning"),
        ]
    )
    @app_commands.autocomplete(game=game_autocomplete)
    async def daily_test(
        self,
        interaction: discord.Interaction,
        game: str,
        event: app_commands.Choice[str],
    ) -> None:
        if not await self._require_owner(interaction):
            return
        selected = self._game(game)
        if selected is None:
            return await interaction.response.send_message("❌ Không tìm thấy game.", ephemeral=True)
        destinations = await self._destinations(selected)
        if interaction.guild_id:
            destinations = [
                item for item in destinations if item.guild_id == interaction.guild_id
            ]
        if not destinations:
            return await interaction.response.send_message(
                f"❌ **{selected.name}** chưa có kênh trong server này.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        reset_at = next_reset_at(selected, datetime.now(UTC))
        scheduled = (
            reset_at - timedelta(minutes=self.warning_minutes)
            if event.value == "warning"
            else reset_at
        )
        item = DailyEvent(selected.slug, event.value, scheduled, reset_at)
        try:
            message = await self._announce_event(
                selected,
                item,
                destination=destinations[0],
                test=True,
            )
        except Exception as error:
            logger.exception("Không gửi thử được Daily Reset %s", selected.slug)
            return await interaction.followup.send(
                f"❌ Gửi thử thất bại: `{str(error)[:300]}`", ephemeral=True
            )
        await interaction.followup.send(
            f"✅ Đã gửi card thử: [mở tin nhắn]({message.jump_url}). "
            "Không ping role, không gửi DM và không ghi chống trùng.",
            ephemeral=True,
        )

    @dailyreset.command(name="check", description="[Chủ bot] Kiểm tra lịch đến hạn ngay")
    async def daily_check(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            count = await self.check_due_events()
        except Exception as error:
            self.last_error = str(error)[:500]
            logger.exception("Daily Reset check thủ công thất bại")
            return await interaction.followup.send(
                f"❌ Kiểm tra thất bại: `{str(error)[:300]}`", ephemeral=True
            )
        await interaction.followup.send(
            f"✅ Kiểm tra xong, vừa gửi `{count}` thông báo đến hạn.",
            ephemeral=True,
        )

    async def send_settings_preview(self, setting: GuildNotification) -> str:
        selected = self.games.get(setting.target)
        if selected is None:
            raise RuntimeError("Game Daily Reset này chưa được bật toàn cục.")
        if setting.channel_id is None:
            raise RuntimeError("Chưa chọn kênh thông báo.")
        reset_at = next_reset_at(selected, datetime.now(UTC))
        event = DailyEvent(selected.slug, "reset", reset_at, reset_at)
        message = await self._announce_event(
            selected,
            event,
            destination=setting,
            test=True,
        )
        return message.jump_url


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DailyReset(bot))
