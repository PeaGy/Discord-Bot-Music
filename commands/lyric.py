import discord
import aiohttp
import logging
import urllib.parse
import re

from music.state import get_guild_state


logger = logging.getLogger(__name__)

async def fetch_lyrics(title, artist=""):
    # Clean up song titles for better search results
    clean_title = re.sub(r'\(.*?\)|\[.*?\]', '', title)
    clean_title = re.sub(r'(?i)(official|music video|lyric video|audio|video)', '', clean_title)
    clean_title = " ".join(clean_title.split())
    if not clean_title:
        clean_title = title  # Fallback if cleaning removed everything

    clean_artist = ""
    if artist and artist != "Unknown":
        clean_artist = re.sub(r'(?i)(official|vevo|topic|- topic)', '', artist)
        clean_artist = " ".join(clean_artist.split())

    search_query = f"{clean_title} {clean_artist}".strip()
    query = urllib.parse.quote(search_query)
    url = f"https://lrclib.net/api/search?q={query}"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        # Ưu tiên lời bài hát đồng bộ (có dấu thời gian)
                        return data[0].get("syncedLyrics") or data[0].get("plainLyrics")
        except Exception as e:
            logger.warning("Không lấy được lyrics từ LRCLIB: %s", e)
    return None

async def setup(bot):
    @bot.tree.command(
        name="lyric",
        description="🎵 Displaying The Lyrics Of The Currently Playing Music"
    )
    async def lyric(interaction: discord.Interaction):
        history = get_guild_state(interaction.guild).history

        vc = interaction.guild.voice_client
        if not vc or not (vc.is_playing() or vc.is_paused()):
            embed = discord.Embed(
                title="Không có nhạc đang phát",
                color=0x2b2d31
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        if len(history) == 0:
            embed = discord.Embed(
                description="Không có bài hát nào được phát vào lần cuối.",
                color=0x2b2d31
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        # Lấy bài hát đang phát hiện tại (bài hát cuối cùng được thêm vào lịch sử phát)
        current_song = history[-1]
        title = current_song.get("title", "Unknown")
        artist = current_song.get("author", "")
        
        await interaction.response.defer(ephemeral=False)
        
        lyrics = await fetch_lyrics(title, artist)
        
        if not lyrics:
            embed = discord.Embed(
                description=f"❌ Không tìm thấy lời bài hát cho **{title}** .",
                color=0x2b2d31
            )
            return await interaction.followup.send(embed=embed)
            
        # Cắt bớt lời bài hát nếu chúng vượt quá giới hạn mô tả nhúng của Discord (4096 ký tự).
        if len(lyrics) > 4000:
            lyrics = lyrics[:3997] + "..."
            
        embed = discord.Embed(
            title=f"🎶 Lyrics: {title}",
            description=lyrics,
            color=0x2b2d31
        )
        
        thumbnail = current_song.get("thumbnail")
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
            
        await interaction.followup.send(embed=embed)
