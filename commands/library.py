import logging

import discord
from discord import app_commands
from discord.ext import commands

import music_library
from music.player import play_next
from music.state import get_guild_state


logger = logging.getLogger(__name__)


def format_duration(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return "Unknown"
    minutes, remaining = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remaining:02d}"
    return f"{minutes}:{remaining:02d}"


def current_track(interaction: discord.Interaction) -> dict | None:
    vc = interaction.guild.voice_client
    state = get_guild_state(interaction.guild)
    if not vc or not (vc.is_playing() or vc.is_paused()) or not state.history:
        return None
    return state.history[-1]


def track_line(index: int, track: dict) -> str:
    title = discord.utils.escape_markdown(str(track.get("title") or "Unknown"))
    url = str(track.get("url") or "")
    duration = format_duration(track.get("duration"))
    if url.startswith(("https://", "http://")):
        title_text = f"[{title}]({url})"
    else:
        title_text = f"**{title}**"
    return f"`{index}.` {title_text} `[{duration}]`"


async def send_name_error(interaction: discord.Interaction, error: ValueError):
    sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    await sender(f"❌ {error}", ephemeral=True)


class MusicLibrary(commands.Cog):
    playlist = app_commands.Group(
        name="playlist",
        description="Quản lý playlist cá nhân",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="favorite",
        description="Thêm hoặc xóa bài đang phát khỏi danh sách yêu thích",
    )
    async def favorite(self, interaction: discord.Interaction):
        track = current_track(interaction)
        if not track:
            return await interaction.response.send_message(
                "❌ Không có bài nào đang phát.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        result = await music_library.toggle_favorite(
            interaction.guild.id,
            interaction.user.id,
            track,
        )
        if result == "added":
            message = f"❤️ Đã thêm **{track.get('title', 'Unknown')}** vào yêu thích."
        elif result == "removed":
            message = f"💔 Đã xóa **{track.get('title', 'Unknown')}** khỏi yêu thích."
        else:
            message = f"❌ Danh sách yêu thích đã đạt {music_library.MAX_FAVORITES} bài."
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(
        name="favorites",
        description="Xem danh sách nhạc yêu thích của bạn",
    )
    async def favorites(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tracks = await music_library.list_favorites(
            interaction.guild.id,
            interaction.user.id,
        )
        if not tracks:
            return await interaction.followup.send(
                "Bạn chưa lưu bài yêu thích nào trong server này.",
                ephemeral=True,
            )

        description = "\n".join(
            track_line(index, track)
            for index, track in enumerate(tracks, start=1)
        )
        embed = discord.Embed(
            title="❤️ Nhạc yêu thích",
            description=description,
            color=0xED4245,
        )
        embed.set_footer(text=f"Hiển thị {len(tracks)} bài gần nhất")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="recent",
        description="Xem lịch sử nhạc gần đây của server",
    )
    async def recent(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tracks = await music_library.list_recent(interaction.guild.id, limit=15)
        if not tracks:
            return await interaction.followup.send(
                "Server này chưa có lịch sử phát nhạc.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="🕘 Lịch sử nghe gần đây",
            description="\n".join(
                track_line(index, track)
                for index, track in enumerate(tracks, start=1)
            ),
            color=0x2B2D31,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @playlist.command(name="create", description="Tạo playlist cá nhân")
    @app_commands.describe(name="Tên playlist, tối đa 50 ký tự")
    async def playlist_create(self, interaction: discord.Interaction, name: str):
        try:
            result = await music_library.create_playlist(
                interaction.guild.id,
                interaction.user.id,
                name,
            )
        except ValueError as error:
            return await send_name_error(interaction, error)

        if result == "created":
            message = f"✅ Đã tạo playlist **{name.strip()}**."
        elif result == "exists":
            message = "❌ Bạn đã có playlist với tên này."
        else:
            message = f"❌ Bạn chỉ có thể tạo tối đa {music_library.MAX_PLAYLISTS} playlist mỗi server."
        await interaction.response.send_message(message, ephemeral=True)

    @playlist.command(name="list", description="Xem các playlist của bạn")
    async def playlist_list(self, interaction: discord.Interaction):
        playlists = await music_library.list_playlists(
            interaction.guild.id,
            interaction.user.id,
        )
        if not playlists:
            return await interaction.response.send_message(
                "Bạn chưa tạo playlist nào trong server này.",
                ephemeral=True,
            )

        lines = [
            f"`{index}.` **{discord.utils.escape_markdown(item['name'])}** — {item['track_count']} bài"
            for index, item in enumerate(playlists, start=1)
        ]
        embed = discord.Embed(
            title="📚 Playlist của bạn",
            description="\n".join(lines),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @playlist.command(name="add", description="Thêm bài đang phát vào playlist")
    @app_commands.describe(name="Tên playlist đã tạo")
    async def playlist_add(self, interaction: discord.Interaction, name: str):
        track = current_track(interaction)
        if not track:
            return await interaction.response.send_message(
                "❌ Không có bài nào đang phát.",
                ephemeral=True,
            )

        try:
            result = await music_library.add_track_to_playlist(
                interaction.guild.id,
                interaction.user.id,
                name,
                track,
            )
        except ValueError as error:
            return await send_name_error(interaction, error)

        if result == "added":
            message = f"✅ Đã thêm **{track.get('title', 'Unknown')}** vào **{name.strip()}**."
        elif result == "not_found":
            message = "❌ Không tìm thấy playlist này. Dùng `/playlist list` để kiểm tra."
        else:
            message = f"❌ Playlist đã đạt giới hạn {music_library.MAX_PLAYLIST_TRACKS} bài."
        await interaction.response.send_message(message, ephemeral=True)

    @playlist.command(name="show", description="Xem các bài trong playlist")
    @app_commands.describe(name="Tên playlist")
    async def playlist_show(self, interaction: discord.Interaction, name: str):
        try:
            tracks = await music_library.get_playlist_tracks(
                interaction.guild.id,
                interaction.user.id,
                name,
            )
        except ValueError as error:
            return await send_name_error(interaction, error)

        if tracks is None:
            return await interaction.response.send_message(
                "❌ Không tìm thấy playlist này.",
                ephemeral=True,
            )
        if not tracks:
            return await interaction.response.send_message(
                f"Playlist **{name.strip()}** đang trống.",
                ephemeral=True,
            )

        visible_tracks = tracks[:20]
        embed = discord.Embed(
            title=f"📀 {name.strip()}",
            description="\n".join(
                track_line(index, track)
                for index, track in enumerate(visible_tracks, start=1)
            ),
            color=0x5865F2,
        )
        if len(tracks) > len(visible_tracks):
            embed.set_footer(text=f"Hiển thị 20/{len(tracks)} bài")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @playlist.command(name="play", description="Thêm toàn bộ playlist vào hàng đợi")
    @app_commands.describe(name="Tên playlist")
    async def playlist_play(self, interaction: discord.Interaction, name: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message(
                "❌ Bạn phải vào voice channel trước.",
                ephemeral=True,
            )

        await interaction.response.defer(thinking=True)
        try:
            tracks = await music_library.get_playlist_tracks(
                interaction.guild.id,
                interaction.user.id,
                name,
            )
        except ValueError as error:
            return await interaction.followup.send(f"❌ {error}", ephemeral=True)

        if tracks is None:
            return await interaction.followup.send(
                "❌ Không tìm thấy playlist này.",
                ephemeral=True,
            )
        if not tracks:
            return await interaction.followup.send(
                "❌ Playlist đang trống.",
                ephemeral=True,
            )

        voice_channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if vc and vc.is_connected() and vc.channel != voice_channel:
            return await interaction.followup.send(
                f"❌ Bot đang phát nhạc ở **{vc.channel.name}**.",
                ephemeral=True,
            )

        if not vc or not vc.is_connected():
            try:
                vc = await voice_channel.connect(self_deaf=True)
            except Exception as error:
                logger.warning(
                    "Không kết nối được voice để phát playlist (guild=%s): %s",
                    interaction.guild.id,
                    error,
                )
                return await interaction.followup.send(
                    "❌ Không thể kết nối voice lúc này.",
                    ephemeral=True,
                )

        state = get_guild_state(interaction.guild)
        for track in tracks:
            state.queue.append({**track, "requester": interaction.user})

        await interaction.followup.send(
            f"▶️ Đã thêm **{len(tracks)} bài** từ playlist **{name.strip()}** vào hàng đợi."
        )
        if not vc.is_playing() and not vc.is_paused():
            await play_next(self.bot, vc, interaction.channel)

    @playlist.command(name="delete", description="Xóa playlist của bạn")
    @app_commands.describe(name="Tên playlist cần xóa")
    async def playlist_delete(self, interaction: discord.Interaction, name: str):
        try:
            deleted = await music_library.delete_playlist(
                interaction.guild.id,
                interaction.user.id,
                name,
            )
        except ValueError as error:
            return await send_name_error(interaction, error)

        message = (
            f"🗑️ Đã xóa playlist **{name.strip()}**."
            if deleted
            else "❌ Không tìm thấy playlist này."
        )
        await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot):
    await music_library.init_db()
    await bot.add_cog(MusicLibrary(bot))
