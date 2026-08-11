import discord
from discord import app_commands
from discord.ext import commands

import music_library


def duration_text(seconds):
    minutes = int(seconds or 0) // 60
    return f"{minutes // 60} giờ {minutes % 60} phút" if minutes >= 60 else f"{minutes} phút"


def build_stats_embed(guild, data, title):
    embed = discord.Embed(title=title, color=0x9B59B6)
    embed.description = f"🎧 **{data['plays']} lượt nghe hợp lệ** • ⏱️ **{duration_text(data['seconds'])}**"
    if data["tracks"]:
        embed.add_field(name="🏆 Bài hát nổi bật", value="\n".join(
            f"`{i}.` **{x['title']}** — {x['plays']} lượt" for i, x in enumerate(data["tracks"], 1)), inline=False)
    if data["artists"]:
        embed.add_field(name="🎤 Nghệ sĩ", value="\n".join(
            f"`{i}.` **{x['author']}** — {x['plays']} lượt" for i, x in enumerate(data["artists"], 1)), inline=True)
    if data["requesters"]:
        lines = []
        for i, x in enumerate(data["requesters"], 1):
            member = guild.get_member(x["requester_id"])
            lines.append(f"`{i}.` **{member.display_name if member else 'Thành viên đã rời'}** — {duration_text(x['seconds'])}")
        embed.add_field(name="🎶 Người nghe tích cực", value="\n".join(lines), inline=True)
    embed.set_footer(text="Chỉ tính bài đã nghe ít nhất 30 giây; skip sớm không được tính.")
    return embed


class MusicStats(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="stats", description="Xem thống kê nghe nhạc")
    @app_commands.describe(days="Khoảng thời gian: 7, 30, 90 hoặc 365 ngày", personal="Chỉ xem thống kê của bạn")
    async def stats(self, interaction: discord.Interaction, days: app_commands.Range[int, 7, 365] = 30, personal: bool = False):
        await interaction.response.defer()
        data = await music_library.listening_stats(interaction.guild.id, days, interaction.user.id if personal else None)
        await interaction.followup.send(embed=build_stats_embed(interaction.guild, data, f"📊 Thống kê {days} ngày"))

    @app_commands.command(name="wrapped", description="Tổng kết âm nhạc 30 ngày gần nhất của server")
    async def wrapped(self, interaction: discord.Interaction):
        await interaction.response.defer()
        data = await music_library.listening_stats(interaction.guild.id, 30)
        await interaction.followup.send(embed=build_stats_embed(interaction.guild, data, "✨ Music Wrapped • 30 ngày"))


async def setup(bot): await bot.add_cog(MusicStats(bot))
