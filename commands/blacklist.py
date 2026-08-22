"""Lệnh quản trị quyền trò chuyện với Peto AI."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

import user_memory


logger = logging.getLogger(__name__)


class AIChatBlacklist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _is_owner(self, interaction: discord.Interaction) -> bool:
        if await self.bot.is_owner(interaction.user):
            return True
        await interaction.response.send_message(
            "❌ Chỉ chủ bot mới được phép sử dụng lệnh này.",
            ephemeral=True,
        )
        return False

    @app_commands.command(
        name="blacklist",
        description="🚫 [Chủ bot] Cấm một người dùng trò chuyện với Peto",
    )
    @app_commands.describe(user="Người dùng không được phép chat với Peto")
    async def blacklist(
        self,
        interaction: discord.Interaction,
        user: discord.User,
    ):
        if not await self._is_owner(interaction):
            return
        if user.id == interaction.user.id:
            return await interaction.response.send_message(
                "❌ Bạn không thể blacklist chính mình.",
                ephemeral=True,
            )
        if user.bot:
            return await interaction.response.send_message(
                "❌ Blacklist này chỉ áp dụng cho người dùng Discord.",
                ephemeral=True,
            )

        added = await user_memory.add_ai_blacklist(
            user.id,
            interaction.user.id,
        )
        if not added:
            return await interaction.response.send_message(
                f"ℹ️ {user.mention} đã nằm trong blacklist của Peto.",
                ephemeral=True,
            )

        logger.info(
            "Chủ bot %s đã blacklist AI user %s",
            interaction.user.id,
            user.id,
        )
        await interaction.response.send_message(
            f"🚫 Đã blacklist {user.mention}. Từ giờ họ sẽ nhận: "
            f"**{user_memory.AI_BLACKLIST_DENIAL_MESSAGE}**",
        )

    @app_commands.command(
        name="unblacklist",
        description="✅ [Chủ bot] Cho phép một người dùng trò chuyện lại với Peto",
    )
    @app_commands.describe(user="Người dùng được phép chat lại với Peto")
    async def unblacklist(
        self,
        interaction: discord.Interaction,
        user: discord.User,
    ):
        if not await self._is_owner(interaction):
            return

        removed = await user_memory.remove_ai_blacklist(user.id)
        if not removed:
            return await interaction.response.send_message(
                f"ℹ️ {user.mention} hiện không nằm trong blacklist của Peto.",
                ephemeral=True,
            )

        logger.info(
            "Chủ bot %s đã unblacklist AI user %s",
            interaction.user.id,
            user.id,
        )
        await interaction.response.send_message(
            f"✅ Đã bỏ blacklist {user.mention}. Họ có thể trò chuyện lại với Peto.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AIChatBlacklist(bot))
