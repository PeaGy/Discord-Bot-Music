import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger(__name__)

DEFAULT_WELCOME_GIF_URL = (
    "https://cdn.discordapp.com/attachments/1536451575004790894/"
    "1542518151181377656/teto-kasane-teto.gif"
)
DEFAULT_WELCOME_TITLE = "Chào mừng bạn đến với Peto's Server!"
WELCOME_RED = 0xED4245


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str) -> int:
    try:
        return max(0, int(os.getenv(name, "0").strip() or "0"))
    except (TypeError, ValueError):
        return 0


WELCOME_ENABLED = _env_bool("WELCOME_ENABLED")
WELCOME_CHANNEL_ID = _env_int("WELCOME_CHANNEL_ID")
WELCOME_INCLUDE_BOTS = _env_bool("WELCOME_INCLUDE_BOTS")
WELCOME_TITLE = os.getenv("WELCOME_TITLE", DEFAULT_WELCOME_TITLE).strip() or DEFAULT_WELCOME_TITLE
WELCOME_GIF_FILE = Path(
    os.getenv("WELCOME_GIF_FILE", "assets/welcome_teto.gif")
).resolve()
WELCOME_GIF_URL = os.getenv("WELCOME_GIF_URL", "").strip()


def build_welcome_embed(
    member: discord.Member,
    image_url: str = DEFAULT_WELCOME_GIF_URL,
) -> discord.Embed:
    guild = member.guild
    member_count = guild.member_count
    description = f"Chào mừng {member.mention} đã tham gia **{guild.name}**!"
    if member_count:
        description += f"\nBạn là thành viên thứ **{member_count}** của server."

    avatar_url = member.display_avatar.url
    embed = discord.Embed(
        title=WELCOME_TITLE,
        description=description,
        color=WELCOME_RED,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=member.display_name, icon_url=avatar_url)
    embed.set_thumbnail(url=avatar_url)
    embed.set_image(url=image_url)
    embed.set_footer(text=f"Chào mừng đến với {guild.name}")
    return embed


class Welcome(commands.Cog):
    welcome = app_commands.Group(
        name="welcome",
        description="Xem trước hệ thống chào mừng thành viên mới",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        if WELCOME_ENABLED:
            if WELCOME_CHANNEL_ID:
                logger.info("Welcome message đã bật cho channel=%s", WELCOME_CHANNEL_ID)
            else:
                logger.warning(
                    "WELCOME_ENABLED=true nhưng chưa có WELCOME_CHANNEL_ID; "
                    "bot sẽ chưa gửi tin chào mừng."
                )

    async def _welcome_channel(
        self,
        guild: discord.Guild,
    ) -> discord.abc.Messageable | None:
        if not WELCOME_CHANNEL_ID:
            return None

        channel = guild.get_channel(WELCOME_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(WELCOME_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "Không đọc được welcome channel=%s trong guild=%s",
                    WELCOME_CHANNEL_ID,
                    guild.id,
                )
                return None

        if getattr(channel, "guild", None) != guild or not hasattr(channel, "send"):
            logger.warning(
                "WELCOME_CHANNEL_ID=%s không phải kênh gửi tin của guild=%s",
                WELCOME_CHANNEL_ID,
                guild.id,
            )
            return None
        return channel

    async def _send_welcome(self, member: discord.Member) -> bool:
        channel = await self._welcome_channel(member.guild)
        if channel is None:
            return False

        image_url = WELCOME_GIF_URL or DEFAULT_WELCOME_GIF_URL
        gif_file: discord.File | None = None
        if not WELCOME_GIF_URL and WELCOME_GIF_FILE.is_file():
            gif_file = discord.File(WELCOME_GIF_FILE, filename="welcome_teto.gif")
            image_url = "attachment://welcome_teto.gif"

        try:
            send_options = {}
            if gif_file is not None:
                send_options["file"] = gif_file
            await channel.send(
                content=f"Chào mừng {member.mention}!",
                embed=build_welcome_embed(member, image_url),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=[member],
                ),
                **send_options,
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Không gửi được welcome message cho member=%s guild=%s channel=%s",
                member.id,
                member.guild.id,
                WELCOME_CHANNEL_ID,
            )
            return False
        return True

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not WELCOME_ENABLED:
            return
        if member.bot and not WELCOME_INCLUDE_BOTS:
            return
        await self._send_welcome(member)

    @welcome.command(name="preview", description="Xem trước welcome embed của server")
    async def welcome_preview(self, interaction: discord.Interaction) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "❌ Chỉ chủ bot mới dùng được lệnh này.",
                ephemeral=True,
            )
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Hãy dùng lệnh này trong server.",
                ephemeral=True,
            )
            return

        image_url = WELCOME_GIF_URL or DEFAULT_WELCOME_GIF_URL
        gif_file: discord.File | None = None
        if not WELCOME_GIF_URL and WELCOME_GIF_FILE.is_file():
            gif_file = discord.File(WELCOME_GIF_FILE, filename="welcome_teto.gif")
            image_url = "attachment://welcome_teto.gif"

        send_options = {}
        if gif_file is not None:
            send_options["file"] = gif_file
        await interaction.response.send_message(
            content=f"Chào mừng {interaction.user.mention}!",
            embed=build_welcome_embed(interaction.user, image_url),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
            **send_options,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
