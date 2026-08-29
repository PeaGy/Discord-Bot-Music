"""Per-server Peto Points, collection views, and weekly rankings."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import re
import time
from datetime import date, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from economy_store import (
    EconomyDisabled,
    EconomyStore,
    GuildEconomySettings,
    economy_period_keys,
    get_economy_store,
    previous_week_key,
)


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


CHAT_MIN_POINTS = _env_int("PETO_ECONOMY_CHAT_MIN_POINTS", 8, 1, 10_000)
CHAT_MAX_POINTS = _env_int(
    "PETO_ECONOMY_CHAT_MAX_POINTS", 12, CHAT_MIN_POINTS, 10_000
)
CHAT_COOLDOWN_SECONDS = _env_int(
    "PETO_ECONOMY_CHAT_COOLDOWN_SECONDS", 60, 10, 86_400
)
VOICE_POINTS = _env_int("PETO_ECONOMY_VOICE_POINTS", 5, 1, 10_000)
VOICE_INTERVAL_SECONDS = _env_int(
    "PETO_ECONOMY_VOICE_INTERVAL_SECONDS", 300, 60, 86_400
)
DAILY_EARNING_CAP = _env_int("PETO_ECONOMY_DAILY_CAP", 500, 1, 1_000_000)
SETTINGS_CACHE_SECONDS = 30

RARITY_LABEL = {
    "id3": "3★",
    "id2": "2★",
    "id1": "1★",
    "ego": "E.G.O",
}


def _can_manage_guild(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and (permissions.manage_guild or permissions.administrator))


def _clean_message_for_points(content: str) -> str:
    return re.sub(r"\s+", " ", str(content or "").strip().casefold())


def build_weekly_embed(
    guild_name: str,
    week_key: str,
    rows: list[tuple[int | str, int]],
) -> discord.Embed:
    start = date.fromisoformat(week_key)
    end = start + timedelta(days=6)
    medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")
    lines = []
    for index, (user, points) in enumerate(rows):
        label = (
            f"<@{user}>"
            if isinstance(user, int)
            else str(user)
            .replace("\\", "\\\\")
            .replace("*", "\\*")
            .replace("_", "\\_")
            .replace("`", "\\`")
            .replace("~", "\\~")
            .replace("|", "\\|")
        )
        lines.append(f"{medals[index]} {label} — **{points:,}** điểm")
    embed = discord.Embed(
        title="🏆 Bảng xếp hạng Peto Points tuần",
        description="\n".join(lines) if lines else "Tuần này chưa có ai kiếm điểm.",
        color=0xE7B84B,
    )
    embed.add_field(
        name="Thời gian",
        value=f"`{start.strftime('%d/%m/%Y')}` – `{end.strftime('%d/%m/%Y')}`",
        inline=False,
    )
    embed.set_footer(text=f"{guild_name} • Xếp theo điểm đang có.")
    return embed


class Economy(commands.Cog):
    economy = app_commands.Group(
        name="economy",
        description="Cấu hình hệ thống Peto Points của server",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store: EconomyStore = get_economy_store(bot)
        self._settings_cache: dict[int, tuple[float, GuildEconomySettings]] = {}
        self._voice_eligible_since: dict[tuple[int, int], float] = {}
        self._voice_award_lock = asyncio.Lock()
        self._rng = random.SystemRandom()

    async def cog_load(self) -> None:
        await self.store.init()
        self.voice_rewards.start()
        self.weekly_leaderboard.start()

    async def cog_unload(self) -> None:
        self.voice_rewards.cancel()
        self.weekly_leaderboard.cancel()

    async def _settings(self, guild_id: int, *, fresh: bool = False) -> GuildEconomySettings:
        now = time.monotonic()
        cached = self._settings_cache.get(int(guild_id))
        if not fresh and cached and now - cached[0] < SETTINGS_CACHE_SECONDS:
            return cached[1]
        setting = await self.store.get_settings(int(guild_id))
        self._settings_cache[int(guild_id)] = (now, setting)
        return setting

    def _invalidate_settings(self, guild_id: int) -> None:
        self._settings_cache.pop(int(guild_id), None)

    async def _require_manager(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Lệnh này chỉ dùng được trong server.", ephemeral=True
            )
            return False
        if _can_manage_guild(interaction) or await self.bot.is_owner(interaction.user):
            return True
        await interaction.response.send_message(
            "❌ Bạn cần quyền **Manage Server** để cấu hình economy.",
            ephemeral=True,
        )
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
        ):
            return
        try:
            setting = await self._settings(message.guild.id)
        except Exception:
            logger.exception("Không đọc được cấu hình economy guild=%s", message.guild.id)
            return
        if not setting.enabled or not setting.chat_enabled:
            return
        cleaned = _clean_message_for_points(message.content)
        if len(cleaned) < 4 or cleaned.startswith(("!", "/")):
            return
        content_hash = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
        try:
            await self.store.award_activity(
                message.guild.id,
                message.author.id,
                amount=self._rng.randint(CHAT_MIN_POINTS, CHAT_MAX_POINTS),
                reason="chat",
                source_id=f"message:{message.id}",
                daily_cap=DAILY_EARNING_CAP,
                chat_cooldown=CHAT_COOLDOWN_SECONDS,
                content_hash=content_hash,
            )
        except EconomyDisabled:
            self._invalidate_settings(message.guild.id)
        except Exception:
            logger.exception(
                "Không cộng được điểm chat guild=%s user=%s",
                message.guild.id,
                message.author.id,
            )

    @tasks.loop(seconds=60)
    async def voice_rewards(self) -> None:
        async with self._voice_award_lock:
            now_mono = time.monotonic()
            now_epoch = int(time.time())
            eligible: set[tuple[int, int]] = set()
            for guild in self.bot.guilds:
                try:
                    setting = await self._settings(guild.id)
                except Exception:
                    logger.exception("Không đọc được cấu hình economy guild=%s", guild.id)
                    continue
                if not setting.enabled or not setting.voice_enabled:
                    continue
                for channel in guild.voice_channels:
                    if guild.afk_channel and channel.id == guild.afk_channel.id:
                        continue
                    humans = [member for member in channel.members if not member.bot]
                    if len(humans) < 2:
                        continue
                    for member in humans:
                        key = (guild.id, member.id)
                        eligible.add(key)
                        started = self._voice_eligible_since.setdefault(key, now_mono)
                        if now_mono - started < VOICE_INTERVAL_SECONDS:
                            continue
                        bucket = now_epoch // VOICE_INTERVAL_SECONDS
                        try:
                            await self.store.award_activity(
                                guild.id,
                                member.id,
                                amount=VOICE_POINTS,
                                reason="voice",
                                source_id=f"voice:{bucket}",
                                daily_cap=DAILY_EARNING_CAP,
                            )
                        except EconomyDisabled:
                            self._invalidate_settings(guild.id)
                        except Exception:
                            logger.exception(
                                "Không cộng được điểm voice guild=%s user=%s",
                                guild.id,
                                member.id,
                            )
                        self._voice_eligible_since[key] = now_mono
            for key in tuple(self._voice_eligible_since):
                if key not in eligible:
                    self._voice_eligible_since.pop(key, None)

    @voice_rewards.before_loop
    async def before_voice_rewards(self) -> None:
        await self.bot.wait_until_ready()

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable:
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(channel_id))
        if not hasattr(channel, "send"):
            raise RuntimeError("Kênh leaderboard không gửi được tin nhắn.")
        return channel

    @tasks.loop(minutes=5)
    async def weekly_leaderboard(self) -> None:
        week_key = previous_week_key()
        try:
            settings = await self.store.enabled_leaderboards()
        except Exception:
            logger.exception("Không đọc được cấu hình leaderboard economy")
            return
        for setting in settings:
            if setting.leaderboard_channel_id is None:
                continue
            try:
                if await self.store.weekly_posted(setting.guild_id, week_key):
                    continue
                rows = await self.store.weekly_top(setting.guild_id, week_key, 5)
                # Enabling economy for the first time must not emit an empty historical card.
                if not rows:
                    await self.store.mark_weekly_posted(setting.guild_id, week_key)
                    continue
                destination = await self._resolve_channel(setting.leaderboard_channel_id)
                guild = self.bot.get_guild(setting.guild_id)
                await destination.send(
                    embed=build_weekly_embed(
                        guild.name if guild else f"Server {setting.guild_id}",
                        week_key,
                        rows,
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await self.store.mark_weekly_posted(setting.guild_id, week_key)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound) as error:
                logger.warning(
                    "Không gửi được leaderboard tuần guild=%s channel=%s: %s",
                    setting.guild_id,
                    setting.leaderboard_channel_id,
                    error,
                )
                continue
            except Exception:
                logger.exception(
                    "Không xử lý được leaderboard tuần guild=%s",
                    setting.guild_id,
                )

    @weekly_leaderboard.before_loop
    async def before_weekly_leaderboard(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="points", description="Xem Peto Points của thành viên")
    @app_commands.guild_only()
    @app_commands.describe(member="Thành viên cần xem; bỏ trống để xem của bạn")
    async def points(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        assert interaction.guild is not None
        setting = await self._settings(interaction.guild.id)
        if not setting.enabled:
            return await interaction.response.send_message(
                "💤 Economy chưa được bật trong server này.", ephemeral=True
            )
        target = member or interaction.user
        account = await self.store.get_account(interaction.guild.id, target.id)
        _, week_key = economy_period_keys()
        weekly = await self.store.weekly_points(interaction.guild.id, target.id, week_key)
        summary = await self.store.collection_summary(interaction.guild.id, target.id)
        unique_total = sum(summary.values())
        embed = discord.Embed(
            title=f"💰 Peto Points — {target.display_name}",
            color=0xE7B84B,
        )
        embed.add_field(name="Số dư", value=f"**{account.balance:,}** điểm")
        embed.add_field(name="Đã kiếm tuần này", value=f"**{weekly:,}** điểm")
        embed.add_field(
            name="Extraction Points", value=f"**{account.extraction_points:,}/200**"
        )
        embed.add_field(name="Tổng lượt quay", value=f"`{account.total_pulls:,}`")
        embed.add_field(name="Collection unique", value=f"`{unique_total:,}`")
        embed.add_field(
            name="Tiến độ quay ×10",
            value=f"`{min(account.balance, 1300):,}/1,300`",
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text="Điểm tuần không giảm khi bạn dùng Peto Points")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="collection", description="Xem bộ sưu tập Limbus đã quay")
    @app_commands.guild_only()
    @app_commands.describe(
        member="Thành viên cần xem",
        rarity="Chỉ xem một độ hiếm",
        page="Trang cần xem",
    )
    @app_commands.choices(
        rarity=[
            app_commands.Choice(name="3★ Identity", value="id3"),
            app_commands.Choice(name="2★ Identity", value="id2"),
            app_commands.Choice(name="1★ Identity", value="id1"),
            app_commands.Choice(name="E.G.O", value="ego"),
        ]
    )
    async def collection(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        rarity: app_commands.Choice[str] | None = None,
        page: app_commands.Range[int, 1, 100] = 1,
    ) -> None:
        assert interaction.guild is not None
        target = member or interaction.user
        kind = rarity.value if rarity else None
        per_page = 15
        items, total = await self.store.collection(
            interaction.guild.id,
            target.id,
            item_kind=kind,
            limit=per_page,
            offset=(int(page) - 1) * per_page,
        )
        summary = await self.store.collection_summary(interaction.guild.id, target.id)
        lines = [
            f"{RARITY_LABEL.get(item.item_kind, item.item_kind)} "
            f"**{discord.utils.escape_markdown(item.item_name)}**"
            + (f" ×`{item.copies}`" if item.copies > 1 else "")
            for item in items
        ]
        embed = discord.Embed(
            title=f"📚 Collection — {target.display_name}",
            description="\n".join(lines) if lines else "Chưa có nhân vật ở trang này.",
            color=0xA68B5B,
        )
        embed.add_field(
            name="Unique",
            value=(
                f"3★ `{summary.get('id3', 0)}` • "
                f"2★ `{summary.get('id2', 0)}` • "
                f"1★ `{summary.get('id1', 0)}` • "
                f"E.G.O `{summary.get('ego', 0)}`"
            ),
            inline=False,
        )
        pages = max(1, (total + per_page - 1) // per_page)
        embed.set_footer(text=f"Trang {min(int(page), pages)}/{pages} • {total} kết quả unique")
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="rank", description="Top 5 collection Limbus của server")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        setting = await self._settings(interaction.guild.id)
        if not setting.enabled:
            return await interaction.response.send_message(
                "💤 Economy chưa được bật trong server này.", ephemeral=True
            )
        rows = await self.store.collection_rank(interaction.guild.id, 5)
        medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")
        lines = [
            f"{medals[index]} <@{row.user_id}> — **{row.unique_total} unique**\n"
            f"　3★ `{row.id3}` • 2★ `{row.id2}` • 1★ `{row.id1}` • E.G.O `{row.ego}`"
            for index, row in enumerate(rows)
        ]
        embed = discord.Embed(
            title="🏆 Top Collection Limbus",
            description="\n\n".join(lines) if lines else "Chưa có ai sở hữu nhân vật.",
            color=0xE7B84B,
        )
        embed.set_footer(text="Xếp theo số nhân vật unique; bản trùng không tăng hạng")
        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @economy.command(name="status", description="Xem cấu hình economy của server")
    @app_commands.guild_only()
    async def economy_status(self, interaction: discord.Interaction) -> None:
        if not await self._require_manager(interaction):
            return
        assert interaction.guild is not None
        setting = await self._settings(interaction.guild.id, fresh=True)
        channel = (
            f"<#{setting.leaderboard_channel_id}>"
            if setting.leaderboard_channel_id
            else "chưa đặt"
        )
        await interaction.response.send_message(
            "**Peto Economy**\n"
            f"• Trạng thái: **{'Bật' if setting.enabled else 'Tắt'}**\n"
            f"• Điểm chat: **{'Bật' if setting.chat_enabled else 'Tắt'}**\n"
            f"• Điểm voice: **{'Bật' if setting.voice_enabled else 'Tắt'}**\n"
            f"• Kênh leaderboard: {channel}\n"
            f"• Chat: `{CHAT_MIN_POINTS}–{CHAT_MAX_POINTS}` điểm / "
            f"`{CHAT_COOLDOWN_SECONDS}s`\n"
            f"• Voice: `{VOICE_POINTS}` điểm / `{VOICE_INTERVAL_SECONDS // 60}` phút\n"
            f"• Giới hạn ngày: `{DAILY_EARNING_CAP:,}` điểm",
            ephemeral=True,
        )

    @economy.command(name="enable", description="Bật Peto Points trong server")
    @app_commands.guild_only()
    async def economy_enable(self, interaction: discord.Interaction) -> None:
        if not await self._require_manager(interaction):
            return
        assert interaction.guild is not None
        await self.store.update_settings(
            interaction.guild.id,
            updated_by=interaction.user.id,
            enabled=True,
        )
        self._invalidate_settings(interaction.guild.id)
        await interaction.response.send_message(
            "✅ Đã bật **Peto Economy**. Thành viên bắt đầu nhận điểm từ chat và voice.",
            ephemeral=True,
        )

    @economy.command(name="disable", description="Tắt Peto Points trong server")
    @app_commands.guild_only()
    async def economy_disable(self, interaction: discord.Interaction) -> None:
        if not await self._require_manager(interaction):
            return
        assert interaction.guild is not None
        await self.store.update_settings(
            interaction.guild.id,
            updated_by=interaction.user.id,
            enabled=False,
        )
        self._invalidate_settings(interaction.guild.id)
        await interaction.response.send_message(
            "✅ Đã tắt **Peto Economy**. Dữ liệu điểm và collection vẫn được giữ nguyên.",
            ephemeral=True,
        )

    @economy.command(name="channel", description="Đặt kênh đăng top điểm mỗi tuần")
    @app_commands.guild_only()
    @app_commands.describe(channel="Kênh leaderboard; bỏ trống để xóa cấu hình")
    async def economy_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_manager(interaction):
            return
        assert interaction.guild is not None
        await self.store.update_settings(
            interaction.guild.id,
            updated_by=interaction.user.id,
            leaderboard_channel_id=channel.id if channel else None,
        )
        self._invalidate_settings(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ Kênh leaderboard tuần: {channel.mention if channel else '**đã tắt**'}.",
            ephemeral=True,
        )

    @economy.command(name="earning", description="Bật hoặc tắt một nguồn kiếm điểm")
    @app_commands.guild_only()
    @app_commands.describe(source="Nguồn điểm", enabled="Bật hay tắt")
    @app_commands.choices(
        source=[
            app_commands.Choice(name="Tin nhắn chat", value="chat"),
            app_commands.Choice(name="Kênh voice", value="voice"),
        ]
    )
    async def economy_earning(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str],
        enabled: bool,
    ) -> None:
        if not await self._require_manager(interaction):
            return
        assert interaction.guild is not None
        kwargs = (
            {"chat_enabled": enabled}
            if source.value == "chat"
            else {"voice_enabled": enabled}
        )
        await self.store.update_settings(
            interaction.guild.id,
            updated_by=interaction.user.id,
            **kwargs,
        )
        self._invalidate_settings(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ Đã **{'bật' if enabled else 'tắt'}** điểm từ {source.name}.",
            ephemeral=True,
        )

    @economy.command(name="grant", description="Cộng hoặc trừ Peto Points thủ công")
    @app_commands.guild_only()
    @app_commands.describe(member="Thành viên", amount="Số dương để cộng, số âm để trừ")
    async def economy_grant(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, -1_000_000, 1_000_000],
    ) -> None:
        if not await self._require_manager(interaction):
            return
        assert interaction.guild is not None
        if int(amount) == 0:
            return await interaction.response.send_message(
                "❌ Số điểm thay đổi phải khác 0.", ephemeral=True
            )
        before = await self.store.get_account(interaction.guild.id, member.id)
        account = await self.store.adjust_points(
            interaction.guild.id,
            member.id,
            delta=int(amount),
            source_id=f"admin:{interaction.id}",
            reason=f"admin:{interaction.user.id}",
        )
        applied = account.balance - before.balance
        await interaction.response.send_message(
            f"✅ Đã điều chỉnh **{applied:+,}** Peto Points cho {member.mention}. "
            f"Số dư mới: **{account.balance:,}**.",
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

    @economy.command(name="preview", description="Xem trước bảng xếp hạng điểm tuần")
    @app_commands.guild_only()
    async def economy_preview(self, interaction: discord.Interaction) -> None:
        if not await self._require_manager(interaction):
            return
        assert interaction.guild is not None
        _, week_key = economy_period_keys()
        rows: list[tuple[int | str, int]] = list(
            await self.store.weekly_top(interaction.guild.id, week_key, 5)
        )
        using_sample = not rows
        if using_sample:
            rows = [
                ("Thành viên A", 3_840),
                ("Thành viên B", 2_950),
                ("Thành viên C", 2_410),
                ("Thành viên D", 1_870),
                ("Thành viên E", 1_320),
            ]
        embed = build_weekly_embed(interaction.guild.name, week_key, rows)
        embed.title = f"🔎 Preview • {embed.title}"
        embed.set_footer(
            text=(
                "Dữ liệu mẫu vì tuần này chưa có ai nhận điểm • Preview không được lưu"
                if using_sample
                else "Dữ liệu tuần hiện tại • Preview không được lưu"
            )
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
