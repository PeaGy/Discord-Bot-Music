import discord
from discord import app_commands
from discord.ext import commands

import user_memory


class ResetMemory(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ==========================================
    # 1. TỰ PHỤC VỤ - ai cũng dùng được, chỉ xoá DỮ LIỆU CỦA CHÍNH MÌNH
    # ==========================================
    @app_commands.command(
        name="resetmemory",
        description="🧹 Xoá trí nhớ AI của bạn trong DM và mọi server",
    )
    async def resetmemory(self, interaction: discord.Interaction):
        await user_memory.clear_user(interaction.user.id)
        embed = discord.Embed(
            description=(
                "🧹 Đã xoá sạch trí nhớ trò chuyện của bạn với bot.\n"
                "-# Cài đặt Ẩn danh, nếu đang bật, vẫn được giữ nguyên."
            )
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==========================================
    # 2. ADMIN TỪNG SERVER - chỉ xoá lịch sử chat TRONG SERVER ĐÓ, không đụng
    # tới server khác. Đây là quyền Admin của Discord server (do chủ server
    # A cấp), KHÔNG liên quan gì tới dev/chủ bot.
    # ==========================================
    @app_commands.command(
        name="resetmemoryall",
        description="🧹 [Admin server] Xoá trí nhớ chat của mọi người TRONG SERVER NÀY",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def resetmemoryall(self, interaction: discord.Interaction):
        channel_ids = [c.id for c in interaction.guild.channels]
        await user_memory.clear_guild(interaction.guild.id, channel_ids)
        embed = discord.Embed(
            description=(
                "🧹 Đã xoá lịch sử và bản tóm tắt trí nhớ của mọi người "
                "**trong server này**.\n"
                "-# Trí nhớ DM và các server khác không bị ảnh hưởng."
            )
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @resetmemoryall.error
    async def resetmemoryall_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message(
                "❌ Chỉ admin của server này mới dùng được lệnh này.",
                ephemeral=True,
            )
        elif isinstance(error, app_commands.errors.NoPrivateMessage):
            await interaction.response.send_message(
                "❌ Lệnh này chỉ dùng được trong server, không dùng được ở DM.",
                ephemeral=True,
            )
        else:
            raise error

    # ==========================================
    # 3. DEV / CHỦ BOT - lệnh mạnh nhất, xoá TOÀN BỘ mọi server, mọi người.
    # Kiểm tra bằng bot.is_owner() -> chỉ đúng tài khoản đứng tên chủ ứng
    # dụng bot trên Discord Developer Portal mới dùng được, bất kể họ đang
    # ở server nào.
    # ==========================================
    @app_commands.command(
        name="resetmemoryglobal",
        description="🧹 [Chỉ dev] Xoá TOÀN BỘ trí nhớ, mọi server, mọi người",
    )
    async def resetmemoryglobal(self, interaction: discord.Interaction):
        if not await self.bot.is_owner(interaction.user):
            return await interaction.response.send_message(
                "❌ Chỉ dev (chủ bot) mới dùng được lệnh này.", ephemeral=True
            )

        await user_memory.clear_all()
        embed = discord.Embed(
            description="🧹 Đã xoá sạch **toàn bộ** trí nhớ, mọi server, mọi người."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ResetMemory(bot))
