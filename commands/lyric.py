import io
import logging

import discord
from discord.ext import commands

from lyrics_service import fetch_lyrics, translate_lyrics
from music.state import get_guild_state

logger = logging.getLogger(__name__)


async def send_full(interaction, title, text, suffix="lyrics"):
    if len(text) <= 3900:
        await interaction.response.send_message(embed=discord.Embed(title=title, description=text, color=0x2B2D31), ephemeral=True)
    else:
        file = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"{suffix}.txt")
        await interaction.response.send_message(f"📄 **{title}**", file=file, ephemeral=True)


class LyricsView(discord.ui.View):
    def __init__(self, title, lyrics, owner_id):
        super().__init__(timeout=180)
        self.title, self.lyrics, self.owner_id = title, lyrics, owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dùng `/lyric` để mở lời bài hát riêng của bạn nhé.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Xem toàn bộ", emoji="📄", style=discord.ButtonStyle.primary)
    async def full(self, interaction, _button): await send_full(interaction, self.title, self.lyrics)

    async def translate(self, interaction, language, suffix):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try: text = await translate_lyrics(self.lyrics, language)
        except Exception as error:
            logger.warning("Không dịch được lyrics: %s", error)
            return await interaction.followup.send(f"❌ {error}", ephemeral=True)
        if len(text) <= 3900:
            await interaction.followup.send(embed=discord.Embed(title=f"{self.title} • {language}", description=text, color=0x2B2D31), ephemeral=True)
        else:
            await interaction.followup.send(file=discord.File(io.BytesIO(text.encode()), filename=f"lyrics-{suffix}.txt"), ephemeral=True)

    @discord.ui.button(label="Dịch Việt", emoji="🇻🇳", style=discord.ButtonStyle.secondary)
    async def vi(self, interaction, _button): await self.translate(interaction, "Vietnamese", "vi")

    @discord.ui.button(label="Translate EN", emoji="🇬🇧", style=discord.ButtonStyle.secondary)
    async def en(self, interaction, _button): await self.translate(interaction, "English", "en")


class Lyrics(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @discord.app_commands.command(name="lyric", description="Xem lời bài hát đang phát")
    async def lyric(self, interaction: discord.Interaction):
        state = get_guild_state(interaction.guild)
        vc = interaction.guild.voice_client
        if not vc or not (vc.is_playing() or vc.is_paused()) or not state.history:
            return await interaction.response.send_message("❌ Không có nhạc đang phát.", ephemeral=True)
        song = state.history[-1]
        await interaction.response.defer()
        lyrics = await fetch_lyrics(song.get("title", "Unknown"), song.get("author", ""))
        if not lyrics:
            return await interaction.followup.send(f"❌ Không tìm thấy lời cho **{song.get('title', 'Unknown')}**.")
        preview = lyrics[:950].rsplit("\n", 1)[0] + ("\n…" if len(lyrics) > 950 else "")
        embed = discord.Embed(title=f"🎶 {song.get('title', 'Unknown')}", description=preview, color=0x2B2D31)
        if song.get("thumbnail"): embed.set_thumbnail(url=song["thumbnail"])
        embed.set_footer(text="Bản xem trước • Các nút trả lời riêng tư để tránh spam")
        await interaction.followup.send(embed=embed, view=LyricsView(song.get("title", "Lyrics"), lyrics, interaction.user.id))


async def setup(bot): await bot.add_cog(Lyrics(bot))
