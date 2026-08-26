"""Theo dõi video mới từ kênh YouTube chính thức của Project Moon."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiohttp
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks


logger = logging.getLogger(__name__)

DEFAULT_CHANNEL_ID = "UCpqyr6h4RCXCEswHlkSjykA"
DEFAULT_CHANNEL_NAME = "ProjectMoon Official"
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
ATOM_NS = "http://www.w3.org/2005/Atom"
YOUTUBE_NS = "http://www.youtube.com/xml/schemas/2015"
MEDIA_NS = "http://search.yahoo.com/mrss/"


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
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        snowflake = int(value)
    except ValueError:
        logger.warning("%s phải là Discord ID dạng số; bỏ qua giá trị này", name)
        return None
    return snowflake if snowflake > 0 else None


def _keywords_from_env() -> tuple[str, ...]:
    raw = os.getenv(
        "PROJECT_MOON_YOUTUBE_KEYWORDS",
        "LimbusCompany,Limbus Company",
    )
    return tuple(
        keyword.strip().casefold()
        for keyword in raw.split(",")
        if keyword.strip()
    )


@dataclass(frozen=True, slots=True)
class YouTubeFeedEntry:
    video_id: str
    title: str
    url: str
    published: datetime | None
    updated: datetime | None
    description: str
    thumbnail_url: str
    channel_name: str


def _node_text(parent: ET.Element, path: str) -> str:
    node = parent.find(path)
    return (node.text or "").strip() if node is not None else ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_youtube_feed(payload: bytes | str) -> list[YouTubeFeedEntry]:
    """Đọc Atom feed của YouTube thành dữ liệu độc lập với Discord."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise ValueError("YouTube RSS trả về XML không hợp lệ") from error

    channel_name = _node_text(root, f"{{{ATOM_NS}}}title") or DEFAULT_CHANNEL_NAME
    entries: list[YouTubeFeedEntry] = []
    for node in root.findall(f"{{{ATOM_NS}}}entry"):
        video_id = _node_text(node, f"{{{YOUTUBE_NS}}}videoId")
        title = _node_text(node, f"{{{ATOM_NS}}}title")
        link = node.find(f"{{{ATOM_NS}}}link[@rel='alternate']")
        url = (link.get("href") or "").strip() if link is not None else ""
        if not video_id or not title:
            continue
        if not url:
            url = f"https://www.youtube.com/watch?v={video_id}"

        media_group = node.find(f"{{{MEDIA_NS}}}group")
        description = ""
        thumbnail_url = ""
        if media_group is not None:
            description = _node_text(
                media_group,
                f"{{{MEDIA_NS}}}description",
            )
            thumbnail = media_group.find(f"{{{MEDIA_NS}}}thumbnail")
            if thumbnail is not None:
                thumbnail_url = (thumbnail.get("url") or "").strip()

        entries.append(
            YouTubeFeedEntry(
                video_id=video_id,
                title=title,
                url=url,
                published=_parse_datetime(
                    _node_text(node, f"{{{ATOM_NS}}}published")
                ),
                updated=_parse_datetime(
                    _node_text(node, f"{{{ATOM_NS}}}updated")
                ),
                description=description,
                thumbnail_url=thumbnail_url,
                channel_name=channel_name,
            )
        )
    return entries


def matches_limbus_keywords(
    entry: YouTubeFeedEntry,
    keywords: tuple[str, ...],
) -> bool:
    """Danh sách từ khóa rỗng có nghĩa là thông báo mọi video của kênh."""
    if not keywords:
        return True
    haystack = f"{entry.title}\n{entry.description}".casefold()
    return any(keyword in haystack for keyword in keywords)


def build_video_embed(
    entry: YouTubeFeedEntry,
    *,
    preview: bool = False,
) -> discord.Embed:
    heading = "🧪 Xem thử thông báo Project Moon" if preview else "🎬 Project Moon vừa đăng video mới"
    description = entry.description.strip()
    if len(description) > 500:
        description = description[:497].rstrip() + "…"
    if not description:
        description = "Video mới từ kênh YouTube chính thức của Project Moon."

    embed = discord.Embed(
        title=entry.title[:256],
        url=entry.url,
        description=description,
        color=0xFF0033,
        timestamp=entry.published,
    )
    embed.set_author(
        name=heading,
        url=f"https://www.youtube.com/channel/{DEFAULT_CHANNEL_ID}",
    )
    if entry.thumbnail_url:
        embed.set_image(url=entry.thumbnail_url)
    embed.add_field(
        name="Kênh",
        value=entry.channel_name or DEFAULT_CHANNEL_NAME,
        inline=True,
    )
    embed.add_field(
        name="Xem video",
        value=f"[Mở trên YouTube]({entry.url})",
        inline=True,
    )
    footer = "Nguồn chính thức • YouTube ProjectMoon Official"
    if preview:
        footer += " • Bản xem thử, chưa đánh dấu đã gửi"
    embed.set_footer(text=footer)
    return embed


class ProjectMoonYouTube(commands.Cog):
    projectmoon = app_commands.Group(
        name="projectmoon",
        description="Theo dõi YouTube chính thức của Project Moon",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.enabled = _env_bool("PROJECT_MOON_YOUTUBE_ENABLED", False)
        self.discord_channel_id = _optional_snowflake(
            "PROJECT_MOON_YOUTUBE_DISCORD_CHANNEL_ID"
        )
        self.mention_role_id = _optional_snowflake(
            "PROJECT_MOON_YOUTUBE_MENTION_ROLE_ID"
        )
        self.youtube_channel_id = (
            os.getenv("PROJECT_MOON_YOUTUBE_CHANNEL_ID", DEFAULT_CHANNEL_ID).strip()
            or DEFAULT_CHANNEL_ID
        )
        self.poll_minutes = _env_int(
            "PROJECT_MOON_YOUTUBE_POLL_MINUTES",
            10,
            2,
            1440,
        )
        self.keywords = _keywords_from_env()
        self.db_path = Path(
            os.getenv("PROJECT_MOON_YOUTUBE_DB", "youtube_notifications.db")
        )
        self.http_timeout = aiohttp.ClientTimeout(total=20)
        self.poll_lock = asyncio.Lock()
        self.last_success_at: float | None = None
        self.last_error: str | None = None
        self.last_announced_count = 0
        self.youtube_poll.change_interval(minutes=self.poll_minutes)

    async def cog_load(self) -> None:
        await self._init_db()
        if not self.enabled:
            logger.info(
                "Project Moon YouTube notifier đang tắt; dùng "
                "PROJECT_MOON_YOUTUBE_ENABLED=true để bật"
            )
            return
        if self.discord_channel_id is None:
            self.enabled = False
            logger.warning(
                "Project Moon YouTube notifier bị tắt vì thiếu "
                "PROJECT_MOON_YOUTUBE_DISCORD_CHANNEL_ID"
            )
            return
        self.youtube_poll.start()
        logger.info(
            "Project Moon YouTube notifier sẵn sàng: channel=%s, poll=%s phút, "
            "keywords=%s",
            self.discord_channel_id,
            self.poll_minutes,
            self.keywords or "tất cả video",
        )

    async def cog_unload(self) -> None:
        self.youtube_poll.cancel()

    async def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS youtube_seen_videos (
                    source_id TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_at TEXT,
                    first_seen_at INTEGER NOT NULL,
                    announced_at INTEGER,
                    PRIMARY KEY (source_id, video_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS youtube_notifier_meta (
                    source_id TEXT PRIMARY KEY,
                    initialized_at INTEGER NOT NULL
                )
                """
            )
            await db.commit()

    async def _fetch_feed(self) -> list[YouTubeFeedEntry]:
        url = FEED_URL.format(channel_id=self.youtube_channel_id)
        headers = {
            "Accept": "application/atom+xml, application/xml;q=0.9",
            "User-Agent": "PetoDiscordBot/1.0 ProjectMoonYouTubeNotifier",
        }
        async with aiohttp.ClientSession(
            timeout=self.http_timeout,
            headers=headers,
        ) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                payload = await response.read()
        entries = parse_youtube_feed(payload)
        if not entries:
            raise RuntimeError("YouTube RSS không trả về video nào")
        return entries

    async def _is_initialized(self, db: aiosqlite.Connection) -> bool:
        cursor = await db.execute(
            "SELECT 1 FROM youtube_notifier_meta WHERE source_id = ?",
            (self.youtube_channel_id,),
        )
        return await cursor.fetchone() is not None

    async def _mark_seen(
        self,
        db: aiosqlite.Connection,
        entry: YouTubeFeedEntry,
        *,
        announced: bool,
    ) -> None:
        now = int(time.time())
        await db.execute(
            """
            INSERT INTO youtube_seen_videos (
                source_id, video_id, title, published_at,
                first_seen_at, announced_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, video_id) DO UPDATE SET
                title = excluded.title,
                published_at = excluded.published_at,
                announced_at = COALESCE(
                    youtube_seen_videos.announced_at,
                    excluded.announced_at
                )
            """,
            (
                self.youtube_channel_id,
                entry.video_id,
                entry.title,
                entry.published.isoformat() if entry.published else None,
                now,
                now if announced else None,
            ),
        )

    async def _seen_ids(
        self,
        db: aiosqlite.Connection,
        entries: list[YouTubeFeedEntry],
    ) -> set[str]:
        ids = [entry.video_id for entry in entries]
        if not ids:
            return set()
        placeholders = ",".join("?" for _ in ids)
        cursor = await db.execute(
            f"""
            SELECT video_id FROM youtube_seen_videos
            WHERE source_id = ? AND video_id IN ({placeholders})
            """,
            (self.youtube_channel_id, *ids),
        )
        return {str(row[0]) for row in await cursor.fetchall()}

    async def _seed_existing(
        self,
        db: aiosqlite.Connection,
        entries: list[YouTubeFeedEntry],
    ) -> None:
        for entry in entries:
            await self._mark_seen(db, entry, announced=False)
        await db.execute(
            """
            INSERT OR IGNORE INTO youtube_notifier_meta (source_id, initialized_at)
            VALUES (?, ?)
            """,
            (self.youtube_channel_id, int(time.time())),
        )
        await db.commit()

    async def _resolve_destination(self):
        if self.discord_channel_id is None:
            raise RuntimeError("Chưa cấu hình kênh Discord nhận thông báo")
        channel = self.bot.get_channel(self.discord_channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.discord_channel_id)
        if not hasattr(channel, "send"):
            raise RuntimeError("Discord ID đích không phải kênh có thể gửi tin nhắn")
        return channel

    async def _announce(self, entry: YouTubeFeedEntry) -> None:
        destination = await self._resolve_destination()
        content = (
            f"<@&{self.mention_role_id}>"
            if self.mention_role_id is not None
            else None
        )
        await destination.send(
            content=content,
            embed=build_video_embed(entry),
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                users=False,
                roles=self.mention_role_id is not None,
            ),
        )

    async def poll_once(self) -> tuple[str, int]:
        """Kiểm tra một lần; trả trạng thái và số video vừa thông báo."""
        async with self.poll_lock:
            entries = await self._fetch_feed()
            async with aiosqlite.connect(self.db_path) as db:
                if not await self._is_initialized(db):
                    await self._seed_existing(db, entries)
                    self.last_success_at = time.time()
                    self.last_error = None
                    self.last_announced_count = 0
                    logger.info(
                        "Đã ghi nhận %s video Project Moon hiện có; "
                        "không gửi ngược video cũ",
                        len(entries),
                    )
                    return "seeded", 0

                seen_ids = await self._seen_ids(db, entries)
                unseen = [entry for entry in reversed(entries) if entry.video_id not in seen_ids]
                announced_count = 0
                for entry in unseen:
                    should_announce = matches_limbus_keywords(entry, self.keywords)
                    if should_announce:
                        await self._announce(entry)
                        announced_count += 1
                    await self._mark_seen(db, entry, announced=should_announce)
                    await db.commit()

                await db.execute(
                    """
                    DELETE FROM youtube_seen_videos
                    WHERE source_id = ? AND video_id NOT IN (
                        SELECT video_id FROM youtube_seen_videos
                        WHERE source_id = ?
                        ORDER BY first_seen_at DESC
                        LIMIT 500
                    )
                    """,
                    (self.youtube_channel_id, self.youtube_channel_id),
                )
                await db.commit()

            self.last_success_at = time.time()
            self.last_error = None
            self.last_announced_count = announced_count
            return "checked", announced_count

    @tasks.loop(minutes=10)
    async def youtube_poll(self) -> None:
        try:
            status, count = await self.poll_once()
            if status == "checked" and count:
                logger.info("Đã thông báo %s video Project Moon mới", count)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.last_error = str(error)[:500]
            logger.exception("Không thể kiểm tra YouTube Project Moon: %s", error)

    @youtube_poll.before_loop
    async def before_youtube_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def _require_owner(self, interaction: discord.Interaction) -> bool:
        if await self.bot.is_owner(interaction.user):
            return True
        await interaction.response.send_message(
            "❌ Chỉ chủ bot mới được dùng nhóm lệnh này.",
            ephemeral=True,
        )
        return False

    @projectmoon.command(
        name="status",
        description="Xem trạng thái hệ thống thông báo YouTube Project Moon",
    )
    async def projectmoon_status(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        destination = (
            f"<#{self.discord_channel_id}>"
            if self.discord_channel_id is not None
            else "chưa cấu hình"
        )
        last_check = (
            f"<t:{int(self.last_success_at)}:R>"
            if self.last_success_at is not None
            else "chưa có"
        )
        keywords = ", ".join(self.keywords) if self.keywords else "mọi video"
        message = (
            f"**Project Moon YouTube notifier**\n"
            f"• Trạng thái: **{'Đang bật' if self.enabled else 'Đang tắt'}**\n"
            f"• Kênh Discord: {destination}\n"
            f"• Chu kỳ: `{self.poll_minutes}` phút\n"
            f"• Bộ lọc: `{keywords}`\n"
            f"• Lần thành công gần nhất: {last_check}\n"
            f"• Số video vừa gửi: `{self.last_announced_count}`"
        )
        if self.last_error:
            message += f"\n• Lỗi gần nhất: `{self.last_error[:250]}`"
        await interaction.response.send_message(message, ephemeral=True)

    @projectmoon.command(
        name="preview",
        description="Xem thử card video mới nhất mà không gửi thông báo",
    )
    async def projectmoon_preview(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            entries = await self._fetch_feed()
            matching = [
                entry
                for entry in entries
                if matches_limbus_keywords(entry, self.keywords)
            ]
            entry = matching[0] if matching else entries[0]
            await interaction.followup.send(
                embed=build_video_embed(entry, preview=True),
                ephemeral=True,
            )
        except Exception as error:
            logger.exception("Không thể xem thử YouTube Project Moon: %s", error)
            await interaction.followup.send(
                f"❌ Không đọc được YouTube RSS lúc này: `{str(error)[:300]}`",
                ephemeral=True,
            )

    @projectmoon.command(
        name="check",
        description="Yêu cầu kiểm tra video mới ngay lập tức",
    )
    async def projectmoon_check(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        if self.discord_channel_id is None:
            return await interaction.response.send_message(
                "❌ Chưa cấu hình `PROJECT_MOON_YOUTUBE_DISCORD_CHANNEL_ID`.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            status, count = await self.poll_once()
        except Exception as error:
            self.last_error = str(error)[:500]
            logger.exception("Kiểm tra YouTube Project Moon thủ công thất bại")
            return await interaction.followup.send(
                f"❌ Kiểm tra thất bại: `{str(error)[:300]}`",
                ephemeral=True,
            )
        if status == "seeded":
            message = (
                "✅ Đã ghi nhận danh sách video hiện tại làm mốc ban đầu. "
                "Bot không gửi lại video cũ; video mới sau mốc này sẽ được thông báo."
            )
        else:
            message = f"✅ Kiểm tra xong, vừa gửi `{count}` video mới."
        await interaction.followup.send(message, ephemeral=True)

    @projectmoon.command(
        name="test",
        description="Gửi thử một card vào kênh đích, không ping và không lưu trạng thái",
    )
    async def projectmoon_test(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        if self.discord_channel_id is None:
            return await interaction.response.send_message(
                "❌ Chưa cấu hình `PROJECT_MOON_YOUTUBE_DISCORD_CHANNEL_ID`.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            entries = await self._fetch_feed()
            matching = [
                entry
                for entry in entries
                if matches_limbus_keywords(entry, self.keywords)
            ]
            entry = matching[0] if matching else entries[0]
            destination = await self._resolve_destination()
            sent = await destination.send(
                embed=build_video_embed(entry, preview=True),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as error:
            logger.exception("Không thể gửi thử thông báo Project Moon")
            return await interaction.followup.send(
                f"❌ Gửi thử thất bại: `{str(error)[:300]}`",
                ephemeral=True,
            )
        await interaction.followup.send(
            f"✅ Đã gửi card thử vào <#{self.discord_channel_id}>: "
            f"[mở tin nhắn]({sent.jump_url}). Không ping role và không đổi dữ liệu chống trùng.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProjectMoonYouTube(bot))
