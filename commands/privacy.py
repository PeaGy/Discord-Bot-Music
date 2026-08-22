import discord
from discord import app_commands
from discord.ext import commands

import user_memory


PRIVATE_GREETING = (
    "🔒 **Cuộc trò chuyện riêng với Peto đã sẵn sàng.**\n\n"
    "Bạn chỉ cần nhắn tin bình thường ở đây, không cần mention bot. "
    "Ảnh đính kèm và Study Mode vẫn hoạt động.\n\n"
    "Dùng `/andanh` nếu bạn muốn nội dung chỉ được giữ tạm trong RAM và "
    "không lưu vào cơ sở dữ liệu. Dùng `/resetmemory` để xóa toàn bộ trí nhớ "
    "đã lưu của chính bạn.\n\n"
    "-# Thành viên trong server không xem được DM này."
)


class Privacy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await user_memory.init_db()

    @app_commands.command(
        name="private",
        description="🔒 Mở cuộc trò chuyện riêng với Peto qua DM",
    )
    async def private(self, interaction: discord.Interaction):
        if await user_memory.is_ai_blacklisted(interaction.user.id):
            return await interaction.response.send_message(
                user_memory.AI_BLACKLIST_DENIAL_MESSAGE,
                ephemeral=interaction.guild is not None,
            )
        if interaction.guild is None:
            return await interaction.response.send_message(PRIVATE_GREETING)

        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.user.send(PRIVATE_GREETING)
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Peto không thể nhắn DM cho bạn. Hãy cho phép tin nhắn trực "
                "tiếp từ thành viên server rồi thử lại `/private`.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.followup.send(
                "❌ Discord chưa thể mở DM lúc này. Hãy thử lại sau một chút.",
                ephemeral=True,
            )

        await interaction.followup.send(
            "✅ Peto đã nhắn riêng cho bạn. Hãy mở mục **Tin nhắn trực tiếp** để trò chuyện.",
            ephemeral=True,
        )

    @app_commands.command(
        name="andanh",
        description="🕶️ Bật hoặc tắt chế độ Ẩn danh tại nơi đang trò chuyện",
    )
    async def anonymous(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        scope = user_memory.scope_for_guild(guild_id)
        enabled = not await user_memory.is_anonymous_mode(
            interaction.user.id,
            scope,
        )
        await user_memory.set_anonymous_mode(
            interaction.user.id,
            scope,
            enabled,
        )

        place = "DM" if interaction.guild is None else "server này"
        if enabled:
            message = (
                f"🕶️ **Đã bật Ẩn danh cho {place}.**\n"
                "Peto sẽ không đọc hoặc ghi trí nhớ SQLite trong phạm vi này. "
                "Ngữ cảnh mới chỉ được giữ tạm trong RAM và sẽ mất khi tắt chế "
                "độ hoặc bot khởi động lại.\n"
                "-# Nội dung vẫn được gửi đến Grok/xAI để tạo câu trả lời."
            )
        else:
            message = (
                f"👁️ **Đã tắt Ẩn danh cho {place}.**\n"
                "Ngữ cảnh tạm vừa được xóa. Peto sẽ tiếp tục sử dụng và lưu trí "
                "nhớ bình thường trong phạm vi này."
            )

        await interaction.response.send_message(
            message,
            ephemeral=interaction.guild is not None,
        )


async def setup(bot):
    await bot.add_cog(Privacy(bot))
