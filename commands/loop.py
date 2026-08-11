import discord

from music.state import get_guild_state


LOOP_MODES = {
    "off": ("track", "🔂 **Lặp lại bài hiện tại**"),
    "track": ("queue", "🔁 **Lặp lại toàn bộ hàng đợi**"),
    "queue": ("off", "➡️ **Đã tắt chế độ lặp**"),
}


async def setup(bot):
    @bot.tree.command(
        name="loop",
        description="Chuyển chế độ lặp: tắt, lặp bài hoặc lặp hàng đợi",
    )
    async def loop(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not (vc.is_playing() or vc.is_paused()):
            embed = discord.Embed(description="❌ Không có nhạc đang phát.")
            return await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )

        state = get_guild_state(interaction.guild)
        state.loop_mode, description = LOOP_MODES.get(
            state.loop_mode,
            LOOP_MODES["off"],
        )

        await interaction.response.send_message(
            embed=discord.Embed(description=description),
            ephemeral=True,
        )
