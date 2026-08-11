# commands/247.py
import discord
from discord import app_commands
from discord.ext import commands
from music.state import get_guild_state


class AlwaysOn(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="247",
        description="Toggle 24/7 Mode"
    )
    async def toggle_247(self, interaction: discord.Interaction):

        if not interaction.user.voice:
            embed = discord.Embed(
                title="**You must be in a voice channel**"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        state = get_guild_state(interaction.guild)

        if state.always_on:
            state.always_on = False

            embed = discord.Embed(
                title="24/7 Mode Disable",
                description="Quả lê will automatically leave the channel if it is not turned on"
            )

            await interaction.response.send_message(embed=embed)

        else:
            state.always_on = True

            embed = discord.Embed(
                title="247 Mode Enabled",
                description="/247 again to disable mode"
            )

            await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(AlwaysOn(bot))
