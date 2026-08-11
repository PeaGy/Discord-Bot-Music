import discord
from discord import app_commands
from discord.ext import commands
from music.state import get_guild_state

class AutoPlay(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="autoplay", description="🔀 Toggle Autoplay")
    async def autoplay(self, interaction: discord.Interaction):

        state = get_guild_state(interaction.guild)

        if state.autoplay:
            state.autoplay = False
            embed = discord.Embed(description="**Autoplay disabled**")
            return await interaction.response.send_message(embed=embed)

        state.autoplay = True
        embed = discord.Embed(description="**Autoplay enabled**")
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(AutoPlay(bot))
