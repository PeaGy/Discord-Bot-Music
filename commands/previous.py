import discord
from discord import app_commands
from discord.ext import commands

from music.player import play_next
from music.state import get_guild_state


class Previous(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="previous",
        description="Play Previous Music"
    )
    async def previous(self, interaction: discord.Interaction):

        vc = interaction.guild.voice_client
        state = get_guild_state(interaction.guild)
        queue = state.queue
        history = state.history

        # 🔒 Bots must be in voice
        if not vc or not vc.is_connected():
            embed = discord.Embed(
                description=(
                    "❌ **Error**\n"
                    "Bot is not connected to a voice channel"
                )
            )
            return await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

        # 🚫 No previous song
        if len(history) < 2:
            embed = discord.Embed(
                description=(
                    "❌ **Error**\n"
                    "No previous song available"
                )
            )
            return await interaction.response.send_message(
                embed=embed
            )

        # 🎵 Take the previous song
        current_song = history.pop()
        previous_song = history.pop()

        # Put back in the queue
        queue.appendleft(current_song)
        queue.appendleft(previous_song)

        # 🛑 Stop the song now
        if vc.is_playing() or vc.is_paused():
            vc.is_previous_action = True
            vc.stop()
        else:
            await play_next(self.bot, vc, interaction.channel)

        # 🎨 EMBED SUCCESS (TITLE BECOMES DESCRIPTION)
        embed = discord.Embed(
            description=(
                "⏮️ **Playing Previous Song**"
            )
        )

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Previous(bot))
