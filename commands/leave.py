import discord
from music.state import get_guild_state

async def setup(bot):

    @bot.tree.command(
        name="leave",
        description="Nah quả lê sủi đây"
    )
    async def leave(interaction: discord.Interaction):
        vc = interaction.guild.voice_client

        if not vc:
            embed = discord.Embed(
                description="**Quả lê không có ở voice chat**"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        state = get_guild_state(interaction.guild)
        state.queue.clear()
        state.cancel_idle_task()
        state.autoplay = False
        state.loop_mode = "off"

        if vc.is_playing() or vc.is_paused():
            vc.stop_request = True
            vc.stop()
        await vc.disconnect()

        embed = discord.Embed(
            description="**Quả lê đã cút khỏi voice chat**"
        )
        await interaction.response.send_message(embed=embed)
