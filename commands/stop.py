import discord
from discord import app_commands
from discord.ext import commands
from music.state import get_guild_state

class Stop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="stop",
        description="⏹️ Stop The Music"
    )
    async def stop(self, interaction: discord.Interaction):
        # 1. Giữ chỗ phản hồi ngay lập tức
        await interaction.response.defer()
        
        vc = interaction.guild.voice_client

        state = get_guild_state(interaction.guild)
        state.queue.clear()
        state.cancel_idle_task()
        state.autoplay = False
        state.loop_mode = "off"

        if vc and vc.is_connected():
            if vc.is_playing() or vc.is_paused():
                vc.stop_request = True
                vc.stop()
            await vc.disconnect()

            embed = discord.Embed(
                description="⏹️ **Stopped Playing**"
            )

            # 2. Dùng followup.send vì đã defer ở trên
            await interaction.followup.send(embed=embed)

        else:
            embed = discord.Embed(
                description="❌ **Quả lê không có ở kênh voice chat nào**"
            )

            # 3. Dùng followup.send vì đã defer ở trên
            await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Stop(bot))
