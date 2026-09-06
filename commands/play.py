import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import music_library
from cache_manager import preload_audio
from music.player import play_next, start_idle_timer
from music.spotify import get_spotify_info, is_spotify_url
from music.state import get_guild_state
from ytdlp_support import (
    audio_fallback_enabled,
    extract_info_with_retry,
    is_transient_ytdlp_error,
    youtube_direct_fallback_enabled,
    youtube_ydl_options,
)

logger = logging.getLogger(__name__)


def is_soundcloud_url(text): return "soundcloud.com" in text
def is_youtube_url(text): return "youtube.com" in text or "youtu.be" in text


def format_duration(seconds):
    minutes, seconds = divmod(int(seconds or 0), 60)
    return f"{minutes}:{seconds:02d}"


def _has_playable_audio_format(info: dict) -> bool:
    return any(
        item.get("url") and str(item.get("acodec") or "").casefold() != "none"
        for item in info.get("formats") or []
    )


def _extraction_has_playable_audio(info: dict | None) -> bool:
    if not info:
        return False
    if "entries" in info:
        info = next((entry for entry in info["entries"] if entry), None)
    return bool(info and _has_playable_audio_format(info))


def _audio_fallback_search_seed(query: str) -> dict:
    """Keep a failed keyword search playable without inventing metadata."""
    return {
        "title": query.strip(),
        "author": "Unknown",
        "duration": 0,
        "url": query.strip(),
        "thumbnail": None,
        "source": "youtube",
        "youtube_metadata_failed": True,
    }


def get_song_info(query: str):
    fallback_eligible = (
        audio_fallback_enabled() and not is_soundcloud_url(query)
    )
    options = youtube_ydl_options(
        {"format": "bestaudio/best", "quiet": True, "noplaylist": True}
    )
    direct_fallback_eligible = bool(
        not is_soundcloud_url(query)
        and options.get("proxy")
        and youtube_direct_fallback_enabled()
    )
    metadata_tolerant = fallback_eligible or direct_fallback_eligible
    if metadata_tolerant:
        # A bot-check response often still contains title/author/thumbnail in
        # the initial page data. Keep that metadata even when no audio format is
        # available. The shared retry helper first tries direct VPS egress, then
        # preserves the metadata for external providers if both routes fail.
        options["ignore_no_formats_error"] = True
    lookup = (
        query
        if is_soundcloud_url(query) or is_youtube_url(query)
        else f"ytsearch1:{query}"
    )
    try:
        info = extract_info_with_retry(
            lookup,
            options,
            download=False,
            result_validator=(
                _extraction_has_playable_audio if metadata_tolerant else None
            ),
        )
    except Exception as error:
        # A plain-text query already is useful SoundCloud search metadata. A
        # failed direct YouTube URL is not: never search using only a video ID.
        if (
            fallback_eligible
            and not is_youtube_url(query)
            and is_transient_ytdlp_error(error)
        ):
            logger.info(
                "YouTube search bị chặn; chuyển query %r vào audio fallback",
                query,
            )
            return _audio_fallback_search_seed(query)
        raise
    if info and "entries" in info:
        info = next((entry for entry in info["entries"] if entry), None)
    if not info:
        raise ValueError("Không tìm thấy kết quả phù hợp.")
    song = {
        "title": info["title"],
        "author": (
            info.get("uploader")
            or info.get("creator")
            or info.get("channel", "Unknown")
        ),
        "duration": info.get("duration", 0),
        "url": info.get("webpage_url") or query,
        "thumbnail": info.get("thumbnail"),
        "source": "soundcloud" if is_soundcloud_url(query) else "youtube",
    }
    if fallback_eligible and not _has_playable_audio_format(info):
        song["youtube_metadata_failed"] = True
        logger.info(
            "YouTube chỉ trả metadata cho %r; chuyển sang audio fallback",
            song["title"],
        )
    elif direct_fallback_eligible and not _has_playable_audio_format(info):
        raise ValueError("YouTube không trả về định dạng audio có thể phát.")
    return song


class DuplicateConfirmView(discord.ui.View):
    def __init__(self, bot, owner_id, guild, channel, vc, song, requester, front):
        super().__init__(timeout=30)
        self.bot, self.owner_id, self.guild, self.channel = bot, owner_id, guild, channel
        self.vc, self.song, self.requester, self.front = vc, song, requester, front

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Đây không phải xác nhận của bạn.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Vẫn thêm", emoji="➕", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, _button):
        queue = get_guild_state(self.guild).queue
        item = {**self.song, "requester": self.requester}
        queue.appendleft(item) if self.front else queue.append(item)
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"✅ Đã thêm **{self.song['title']}** lần nữa.", view=self)
        if not self.vc.is_playing() and not self.vc.is_paused():
            await play_next(self.bot, self.vc, self.channel)

    @discord.ui.button(label="Hủy", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, _button):
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content="Đã hủy, hàng đợi không thay đổi.", view=self)


class Play(commands.Cog):
    def __init__(self, bot): self.bot = bot

    async def _play(self, interaction, query, *, front):
        await interaction.response.defer(thinking=True)
        if not interaction.user.voice:
            return await interaction.followup.send("❌ Bạn cần vào voice channel trước.", ephemeral=True)
        user_channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if vc and vc.channel != user_channel:
            return await interaction.followup.send(f"❌ Bot đang ở **{vc.channel.name}**.", ephemeral=True)
        if not vc:
            try: vc = await user_channel.connect(self_deaf=True)
            except Exception as error:
                logger.warning("Không kết nối được voice: %s", error)
                return await interaction.followup.send("❌ Không thể kết nối voice lúc này.", ephemeral=True)
        loop = asyncio.get_running_loop()
        try:
            song = await loop.run_in_executor(None, get_spotify_info if is_spotify_url(query) else get_song_info, query)
            if not song: raise ValueError("metadata unavailable")
        except Exception as error:
            logger.warning("Không lấy được metadata /play (guild=%s): %s", interaction.guild.id, error)
            if not vc.is_playing() and not vc.is_paused(): await start_idle_timer(vc, channel=interaction.channel)
            return await interaction.followup.send("❌ Không lấy được thông tin bài hát.", ephemeral=True)

        state = get_guild_state(interaction.guild); queue = state.queue
        key = music_library.track_key(song)
        position = next((i for i, item in enumerate(queue, 1) if music_library.track_key(item) == key), None)
        current = bool(state.history and music_library.track_key(state.history[-1]) == key)
        if position or current:
            location = "đang phát" if current else f"đã ở vị trí #{position}"
            view = DuplicateConfirmView(self.bot, interaction.user.id, interaction.guild, interaction.channel, vc, song, interaction.user, front)
            return await interaction.followup.send(f"⚠️ **{song['title']}** {location}. Vẫn thêm lần nữa?", view=view, ephemeral=True)

        item = {**song, "requester": interaction.user}
        queue.appendleft(item) if front else queue.append(item)
        if (
            (vc.is_playing() or vc.is_paused())
            and len(queue) == 1
            and not song.get("youtube_metadata_failed")
            and int(song.get("duration") or 0) <= 600
        ):
            asyncio.create_task(preload_audio(song["url"], delay=3.0))
        embed = discord.Embed(description=f"**{song['title']}** `[{format_duration(song['duration'])}]`")
        embed.set_author(name=f"Đã thêm vào hàng đợi (#{1 if front else len(queue)})", icon_url=interaction.user.display_avatar.url)
        if song.get("thumbnail"): embed.set_thumbnail(url=song["thumbnail"])
        await interaction.followup.send(embed=embed)
        if not vc.is_playing() and not vc.is_paused(): await play_next(self.bot, vc, interaction.channel)

    @app_commands.command(name="play", description="Phát nhạc hoặc thêm vào cuối hàng đợi")
    async def play(self, interaction: discord.Interaction, query: str): await self._play(interaction, query, front=False)

    @app_commands.command(name="playnext", description="Thêm bài vào đầu hàng đợi")
    async def playnext(self, interaction: discord.Interaction, query: str): await self._play(interaction, query, front=True)


async def setup(bot): await bot.add_cog(Play(bot))
