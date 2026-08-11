import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
import logging

from music.spotify import is_spotify_url, get_spotify_info
from music.player import play_next, start_idle_timer
from music.state import get_guild_state
from cache_manager import preload_audio


logger = logging.getLogger(__name__)


# ==============================
# 🔎 URL CHECKER
# ==============================
def is_soundcloud_url(text: str) -> bool:
    return "soundcloud.com" in text

def is_youtube_url(text: str) -> bool:
    return "youtube.com" in text or "youtu.be" in text


# ==============================
# ⏱ FORMAT DURATION
# ==============================
def format_duration(seconds):
    if not seconds:
        return "0:00"

    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


# ==============================
# 🎵 GET SONG INFO
# ==============================
def get_song_info(query: str):
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "noplaylist": True,
        "extractor_args": {"youtube": ["player_client=ios,android,web", "player_skip=webpage"]},
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:

        # 🔗 SoundCloud link → direct extract
        if is_soundcloud_url(query):
            info = ydl.extract_info(query, download=False)

        # 🔗 YouTube link → direct extract
        elif is_youtube_url(query):
            info = ydl.extract_info(query, download=False)

        # 🔍 TÌM LINK → YouTube search ONLY
        else:
            info = ydl.extract_info(
                f"ytsearch1:{query}",
                download=False
            )["entries"][0]

        if "entries" in info:
            info = info["entries"][0]

        return {
            "title": info["title"],
            "author": info.get("uploader") or info.get("creator") or info.get("channel", "Unknown"),
            "duration": info.get("duration", 0),
            "url": info["webpage_url"],
            "thumbnail": info.get("thumbnail"),
            "source": "soundcloud" if is_soundcloud_url(query) else "youtube"
        }


# ==============================
# 🎧 PLAY COMMAND
# ==============================
class Play(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="play", description="🎵 Phát nhạc")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)

        # 🔒 Nguoi dung can o trong voice chat
        if not interaction.user.voice:
            embed = discord.Embed(
                title="Bạn cần ở kênh voice chat dumbass",
            )
            return await interaction.followup.send(embed=embed)

        user_channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client

        # 🔒 BOT dang o kenh vc khac
        if vc and vc.channel != user_channel:
            embed = discord.Embed(
                title="❌ Tôi đang ở kênh voice khác rùi",
                description=f"I'm currently in **{vc.channel.name}**",
            )
            return await interaction.followup.send(embed=embed)

        # 🔌 loi connect
        if not vc:
            try:
                vc = await user_channel.connect(self_deaf=True)
            except asyncio.TimeoutError:
                embed = discord.Embed(
                    title="❌ Connection Timed Out",
                    description="Failed to connect to the voice channel. Discord's voice servers might be slow or blocking UDP traffic."
                )
                return await interaction.followup.send(embed=embed)
            except Exception as e:
                embed = discord.Embed(
                    title="❌ Failed to Connect",
                    description=f"An error occurred: `{str(e)}`"
                )
                return await interaction.followup.send(embed=embed)

        loop = asyncio.get_running_loop()

        try:
            # 🎵 SPOTIFY LINK
            if is_spotify_url(query):
                song = await loop.run_in_executor(None, get_spotify_info, query)
                if not song:
                    embed = discord.Embed(
                        title="❌ Failed to load Spotify link",
                        description="Make sure it's a valid track link, not a playlist or album."
                    )
                    if not vc.is_playing() and not vc.is_paused():
                        await start_idle_timer(vc, channel=interaction.channel)
                    return await interaction.followup.send(embed=embed)
            else:
                song = await loop.run_in_executor(None, get_song_info, query)
        except Exception as error:
            logger.warning(
                "Không lấy được metadata cho /play (guild=%s, query=%r): %s",
                interaction.guild.id,
                query,
                error,
            )
            if not vc.is_playing() and not vc.is_paused():
                await start_idle_timer(vc, channel=interaction.channel)
            return await interaction.followup.send(
                "❌ Không lấy được thông tin bài hát. Hãy kiểm tra URL hoặc thử lại sau.",
                ephemeral=True,
            )

        queue = get_guild_state(interaction.guild).queue
        queue.append({
            **song,
            "requester": interaction.user
        })
        # ==============================
        # ⚡ KÍCH HOẠT TẢI NGẦM NGAY LẬP TỨC
        # ==============================
        duration = int(song.get("duration") or 0)
        is_radio = song.get("source") == "radio"
        
        # Nếu bot đang hát bài khác, và bài vừa thêm < 10 phút -> Tải nền luôn!
        if (vc.is_playing() or vc.is_paused()) and not is_radio and duration <= 600:
            asyncio.create_task(preload_audio(song['url']))

        # 📩 EMBED QUEUE
        embed = discord.Embed(
            description=f"**{song['title']}** `[{format_duration(song['duration'])}]`",
        )

        embed.set_author(
            name=f"Song Added To Queue (#{len(queue)})",
            icon_url=interaction.user.display_avatar.url
        )

        if song.get("thumbnail"):
            embed.set_thumbnail(url=song["thumbnail"])

        await interaction.followup.send(embed=embed)

        # ▶️ AUTO PLAY
        if not vc.is_playing() and not vc.is_paused():
            await play_next(self.bot, vc, interaction.channel)


async def setup(bot):
    await bot.add_cog(Play(bot))
