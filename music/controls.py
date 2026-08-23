import discord
import aiohttp
import logging
import os
import urllib.parse
import re
import time

from cache_manager import AudioDownloadError, temporary_download_mp3
import music_library
from music.state import get_guild_state


logger = logging.getLogger(__name__)


MAX_DOWNLOAD_DURATION = 600
PANEL_ACCENT_COLOR = 0x5865F2
PANEL_MUTED_COLOR = 0x2B2D31
PANEL_ERROR_COLOR = 0xED4245
PANEL_SUCCESS_COLOR = 0x57F287
PANEL_ALLOWED_MENTIONS = discord.AllowedMentions.none()


def safe_mp3_filename(title):
    """Tạo tên file hiển thị an toàn trên Discord và các hệ điều hành."""
    clean_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(title or "audio"))
    clean_title = re.sub(r"\s+", " ", clean_title).strip(" ._")
    clean_title = clean_title[:120].rstrip(" ._") or "audio"
    return f"{clean_title}.mp3"


def _safe_text(value, fallback="Unknown"):
    return discord.utils.escape_markdown(str(value or fallback), as_needed=True)


def _thumbnail_url(value):
    value = str(value or "").strip()
    return value if value.startswith(("https://", "http://")) else None


def _linked_title(track):
    title = _safe_text(track.get("title"), "Unknown Track")
    url = str(track.get("url") or "").strip()
    if url.startswith(("https://", "http://")):
        return f"[{title}]({url})"
    return title


class PlaylistPicker(discord.ui.Select):
    def __init__(self, guild_id, user_id, track, playlists):
        self.guild_id = guild_id
        self.user_id = user_id
        self.track = track
        options = [
            discord.SelectOption(
                label=item["name"][:100],
                value=item["name"],
                description=f"{item['track_count']} bài",
                emoji="📀",
            )
            for item in playlists[:25]
        ]
        super().__init__(placeholder="Chọn playlist để lưu bài này…", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Menu playlist này không thuộc về bạn.", ephemeral=True)
        name = self.values[0]
        result = await music_library.add_track_to_playlist(
            self.guild_id, self.user_id, name, self.track
        )
        messages = {
            "added": f"✅ Đã thêm **{self.track.get('title', 'Unknown')}** vào **{name}**.",
            "duplicate": f"⚠️ Bài này đã có trong **{name}**.",
            "not_found": "❌ Playlist không còn tồn tại.",
            "limit": f"❌ Playlist đã đạt giới hạn {music_library.MAX_PLAYLIST_TRACKS} bài.",
        }
        self.disabled = True
        await interaction.response.edit_message(content=messages[result], view=self.view)


class PlaylistPickerView(discord.ui.View):
    def __init__(self, guild_id, user_id, track, playlists):
        super().__init__(timeout=60)
        self.add_item(PlaylistPicker(guild_id, user_id, track, playlists))


class MusicStatusPanel(discord.ui.LayoutView):
    """Panel V2 gọn cho các trạng thái tải, lỗi, radio, dừng và hết queue."""

    def __init__(
        self,
        *,
        heading,
        description,
        accent_color=PANEL_MUTED_COLOR,
        thumbnail_url=None,
        link_button=None,
    ):
        super().__init__(timeout=None)

        content = discord.ui.TextDisplay(f"### {heading}\n{description}")
        thumbnail_url = _thumbnail_url(thumbnail_url)
        if thumbnail_url:
            header = discord.ui.Section(
                content,
                accessory=discord.ui.Thumbnail(
                    thumbnail_url,
                    description="Ảnh đại diện của nội dung đang phát",
                ),
            )
        else:
            header = content

        children = [header]
        if link_button:
            label, url, emoji = link_button
            row = discord.ui.ActionRow(
                discord.ui.Button(
                    label=label,
                    url=url,
                    emoji=emoji,
                    style=discord.ButtonStyle.link,
                )
            )
            children.extend((discord.ui.Separator(), row))

        self.add_item(
            discord.ui.Container(
                *children,
                accent_color=accent_color,
            )
        )


def create_loading_panel(track, *, long_track=False):
    detail = (
        "Bot đang tải tạm audio gốc của bài dài, phát xong file sẽ tự xóa..."
        if long_track
        else "Bot đang tải và chuẩn hóa âm thanh, vui lòng đợi một chút..."
    )
    return MusicStatusPanel(
        heading="⏳ ĐANG CHUẨN BỊ",
        description=(
            f"**{_safe_text(track.get('title'), 'Unknown')}**\n"
            f"-# {detail}"
        ),
        accent_color=PANEL_ACCENT_COLOR,
        thumbnail_url=track.get("thumbnail"),
    )


def create_error_panel(track):
    return MusicStatusPanel(
        heading="❌ KHÔNG THỂ PHÁT",
        description=(
            f"**{_safe_text(track.get('title'), 'Unknown')}**\n"
            "-# Bài này sẽ được bỏ qua để tiếp tục hàng đợi."
        ),
        accent_color=PANEL_ERROR_COLOR,
        thumbnail_url=track.get("thumbnail"),
    )


def create_stopped_panel():
    return MusicStatusPanel(
        heading="⏹️ ĐÃ DỪNG PHÁT NHẠC",
        description="-# Hàng đợi đã được xóa và bot đã rời kênh thoại.",
        accent_color=PANEL_ERROR_COLOR,
    )


def create_queue_ended_panel(bot_user=None):
    avatar_url = None
    display_name = "Bot"
    if bot_user is not None:
        display_name = _safe_text(getattr(bot_user, "display_name", None), "Bot")
        avatar = getattr(bot_user, "display_avatar", None)
        avatar_url = getattr(avatar, "url", None)

    return MusicStatusPanel(
        heading="✨ HẾT HÀNG ĐỢI",
        description=(
            "Tất cả bài hát đã được phát xong.\n"
            f"-# Dùng `/play` hoặc `/search` để nghe tiếp cùng {display_name}."
        ),
        accent_color=PANEL_SUCCESS_COLOR,
        thumbnail_url=avatar_url,
        link_button=(
            "Đô nết me",
            "https://www.facebook.com/peagy.simp.lo/",
            "🤑",
        ),
    )


def create_radio_panel(track, requester_mention="Autoplay"):
    return MusicStatusPanel(
        heading="🍐 RADIO PANEL",
        description=(
            f"**{_safe_text(track.get('title'), 'Unknown Radio')}**\n"
            f"-# 📻 Phát trực tiếp • Yêu cầu bởi {requester_mention or 'Autoplay'}"
        ),
        accent_color=PANEL_ACCENT_COLOR,
        thumbnail_url=track.get("thumbnail"),
    )


async def edit_panel_message(message, view):
    """Đổi cả panel cũ hoặc V2 sang LayoutView mà không giữ embed/content."""
    return await message.edit(
        content=None,
        embed=None,
        attachments=[],
        view=view,
        allowed_mentions=PANEL_ALLOWED_MENTIONS,
    )


async def send_panel_message(channel, view):
    return await channel.send(
        view=view,
        allowed_mentions=PANEL_ALLOWED_MENTIONS,
    )


class MusicControl(discord.ui.LayoutView):
    def __init__(self, vc, track, current_time=0, queue_length=0, requester_mention=None):
        super().__init__(timeout=None)
        self.vc = vc
        self.track = track
        self.current_time = current_time
        self.queue_length = queue_length
        self.requester_mention = requester_mention

        self.header_text = discord.ui.TextDisplay("")
        thumbnail_url = _thumbnail_url(track.get("thumbnail"))
        if thumbnail_url:
            header = discord.ui.Section(
                self.header_text,
                accessory=discord.ui.Thumbnail(
                    thumbnail_url,
                    description=f"Ảnh bìa của {_safe_text(track.get('title'), 'bài hát')}",
                ),
            )
        else:
            header = self.header_text

        self.progress_text = discord.ui.TextDisplay("")
        self.footer_text = discord.ui.TextDisplay("")
        self.primary_row = discord.ui.ActionRow()
        self.secondary_row = discord.ui.ActionRow()

        # Hàng một: nút Yêu thích được đặt lên đầu theo yêu cầu.
        self.favorite_button = self._add_button(
            self.primary_row, self.favorite, label="Yêu thích", emoji="❤️"
        )
        self.back_button = self._add_button(
            self.primary_row, self.back, label="Back", emoji="⏮️"
        )
        self.pause_button = self._add_button(
            self.primary_row, self.pause, label="Pause", emoji="⏸️"
        )
        self.skip_button = self._add_button(
            self.primary_row, self.skip, label="Skip", emoji="⏭️"
        )
        self.stop_button = self._add_button(
            self.primary_row,
            self.stop_playback,
            label="Stop",
            emoji="⏹️",
            style=discord.ButtonStyle.danger,
        )

        self.loop_button = self._add_button(
            self.secondary_row, self.loop_btn, label="Loop Off", emoji="➡️"
        )
        self.autoplay_button = self._add_button(
            self.secondary_row, self.autoplay_btn, label="Autoplay Off", emoji="🔀"
        )
        self.lyric_button = self._add_button(
            self.secondary_row, self.lyric, label="Lyric", emoji="📝"
        )
        self.download_button = self._add_button(
            self.secondary_row, self.download, label="Tải xuống", emoji="⬇️"
        )
        self.playlist_button = self._add_button(
            self.secondary_row, self.add_to_playlist, label="Playlist", emoji="➕"
        )

        self.panel_container = discord.ui.Container(
            header,
            discord.ui.Separator(),
            self.progress_text,
            self.footer_text,
            discord.ui.Separator(),
            self.primary_row,
            self.secondary_row,
            accent_color=PANEL_ACCENT_COLOR,
        )
        self.add_item(self.panel_container)
        self._sync_button_states()
        self.refresh_layout()

    def _add_button(
        self,
        row,
        handler,
        *,
        label,
        emoji,
        style=discord.ButtonStyle.secondary,
    ):
        button = discord.ui.Button(label=label, emoji=emoji, style=style)

        async def callback(interaction):
            await handler(interaction, button)

        button.callback = callback
        row.add_item(button)
        return button

    def _sync_button_states(self):
        state = get_guild_state(self.vc.guild)

        if state.loop_mode == "track":
            self.loop_button.label = "Loop Track"
            self.loop_button.emoji = "🔂"
            self.loop_button.style = discord.ButtonStyle.success
        elif state.loop_mode == "queue":
            self.loop_button.label = "Loop Queue"
            self.loop_button.emoji = "🔁"
            self.loop_button.style = discord.ButtonStyle.success
        else:
            self.loop_button.label = "Loop Off"
            self.loop_button.emoji = "➡️"
            self.loop_button.style = discord.ButtonStyle.secondary

        if state.autoplay:
            self.autoplay_button.label = "Autoplay On"
            self.autoplay_button.style = discord.ButtonStyle.success
        else:
            self.autoplay_button.label = "Autoplay Off"
            self.autoplay_button.style = discord.ButtonStyle.secondary

        if self.vc.is_paused():
            self.pause_button.label = "Resume"
            self.pause_button.emoji = "▶️"
            self.pause_button.style = discord.ButtonStyle.success
        else:
            self.pause_button.label = "Pause"
            self.pause_button.emoji = "⏸️"
            self.pause_button.style = discord.ButtonStyle.secondary
    # ==========================================
    # HÀM TẠO THANH TIẾN TRÌNH & ĐỊNH DẠNG
    # ==========================================
    def format_time(self, seconds):
        if not seconds: return "0:00"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def get_platform_emoji(self, platform):
        emojis = {
            'youtube': '🔴',
            'spotify': '🟢',
            'soundcloud': '🟠',
            'direct': '🔗'
        }
        return emojis.get(platform.lower() if platform else '', '🎵')

    # ==========================================
    # THỜI GIAN PHÁT HIỆN TẠI (giống player.getCurrentTime() bên JS)
    # ==========================================
    def get_current_time(self):
        """
        Tính số giây đã phát của bài hiện tại, dựa vào các mốc thời gian
        được set trên self.vc (vc.play_start_time / vc.total_paused_duration /
        vc.paused_at) trong player.py. Đây là tính "on-demand" tại thời điểm
        gọi, giống hệt cách JS tính currentTime mỗi khi /nowplaying được gọi
        hoặc embed được cập nhật lại — KHÔNG có timer nào tự chạy nền.
        """
        start = getattr(self.vc, "play_start_time", None)
        if not start:
            return 0

        paused_total = getattr(self.vc, "total_paused_duration", 0)
        elapsed = time.time() - start - paused_total

        paused_at = getattr(self.vc, "paused_at", None)
        if paused_at:
            elapsed -= (time.time() - paused_at)

        return max(0, elapsed)

    def create_progress_bar(self, current, total, length=15):
        """Vẽ thanh tiến trình, port trực tiếp từ createProgressBar() bên nowplaying.js"""
        if not total:
            return "▬" * length

        progress = min(current / total, 1)
        filled_length = round(progress * length)

        filled = "▬" * filled_length
        empty = "▬" * (length - filled_length)
        indicator = "🔘"

        if filled_length == 0:
            return indicator + empty
        elif filled_length >= length:
            return filled + indicator
        else:
            return filled + indicator + empty[1:]

    # ==========================================
    # HÀM BẢO MẬT VOICE
    # ==========================================
    async def check_voice(self, interaction: discord.Interaction):
        if not interaction.user.voice or interaction.user.voice.channel.id != self.vc.channel.id:
            await interaction.response.send_message("❌ **Sussy baka**", ephemeral=True)
            return False
        return True

    # ==========================================
    # ROW 1: CÁC NÚT ĐIỀU KHIỂN CHÍNH
    # ==========================================
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        from music.player import play_next

        state = get_guild_state(interaction.guild)
        history = state.history
        queue = state.queue

        if len(history) < 2:
            return await interaction.response.send_message("❌ Không có bài hát trước đó", ephemeral=True)

        current_song = history.pop()
        previous_song = history.pop()

        queue.appendleft(current_song)
        queue.appendleft(previous_song)

        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.is_previous_action = True
            self.vc.stop()
        else:
            await play_next(interaction.client, self.vc, interaction.channel)

        await interaction.response.defer()

    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        if self.vc.is_playing():
            self.vc.pause()
            self.vc.paused_at = time.time()
            button.label = "Resume"
            button.emoji = "▶️"
            button.style = discord.ButtonStyle.success
        else:
            paused_at = getattr(self.vc, "paused_at", None)
            if paused_at:
                self.vc.total_paused_duration = getattr(self.vc, "total_paused_duration", 0) + (time.time() - paused_at)
                self.vc.paused_at = None
            self.vc.resume()
            button.label = "Pause"
            button.emoji = "⏸️"
            button.style = discord.ButtonStyle.secondary

        # Vẽ lại toàn bộ Components V2 tại đúng thời điểm pause/resume.
        self.refresh_layout()
        await interaction.response.edit_message(
            content=None,
            embed=None,
            attachments=[],
            view=self,
        )

    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.skip_request = True
            self.vc.stop()
        await interaction.response.defer()

    async def stop_playback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        state = get_guild_state(interaction.guild)
        state.now_playing_message = None
        state.queue.clear()

        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.stop_request = True
            self.vc.stop()

        await interaction.response.edit_message(
            content=None,
            embed=None,
            attachments=[],
            view=create_stopped_panel(),
        )

        if self.vc.is_connected():
            await self.vc.disconnect()

        self.stop()

    # ==========================================
    # ROW 2: TÍNH NĂNG BỔ SUNG
    # ==========================================
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        
        state = get_guild_state(interaction.guild)

        # Chu kỳ: off -> track -> queue -> off
        if state.loop_mode == "off":
            state.loop_mode = "track"
            button.label = "Loop Track"
            button.emoji = "🔂"
            button.style = discord.ButtonStyle.success
            msg = "🔂 **Lặp lại bài hiện tại**"
        elif state.loop_mode == "track":
            state.loop_mode = "queue"
            button.label = "Loop Queue"
            button.emoji = "🔁"
            button.style = discord.ButtonStyle.success
            msg = "🔁 **Lặp lại toàn bộ danh sách (Queue)**"
        else:
            state.loop_mode = "off"
            button.label = "Loop Off"
            button.emoji = "➡️"
            button.style = discord.ButtonStyle.secondary
            msg = "➡️ **Đã tắt chế độ lặp**"

        self.refresh_layout()
        await interaction.response.edit_message(
            content=None,
            embed=None,
            attachments=[],
            view=self,
        )
        await interaction.followup.send(msg, ephemeral=True)

    async def autoplay_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        
        from music.player import handle_autoplay
        import asyncio

        guild_id = interaction.guild.id
        state = get_guild_state(guild_id)

        if state.autoplay:
            state.autoplay = False
            button.label = "Autoplay Off"
            button.style = discord.ButtonStyle.secondary
            msg = "❌ **Tắt Autoplay**"
        else:
            state.autoplay = True
            button.label = "Autoplay On"
            button.style = discord.ButtonStyle.success
            msg = "✅ **Bật Autoplay**"
            
            # ==============================
            # ⚡ KÍCH HOẠT TIÊN TRI NGAY LẬP TỨC
            # ==============================
            # Nếu hàng đợi trống và bot đang hát -> Gọi hàm tiên tri ngầm luôn!
            if not state.queue and (self.vc.is_playing() or self.vc.is_paused()):
                bot = interaction.client
                # self.track chính là bài hát đang phát hiện tại
                asyncio.create_task(
                    handle_autoplay(bot, self.vc, interaction.channel, self.track, guild_id, trigger_play=False)
                )

        self.refresh_layout()
        await interaction.response.edit_message(
            content=None,
            embed=None,
            attachments=[],
            view=self,
        )
        await interaction.followup.send(msg, ephemeral=True)

    async def lyric(self, interaction: discord.Interaction, button: discord.ui.Button):
        history = get_guild_state(interaction.guild).history

        vc = self.vc
        if not vc or not (vc.is_playing() or vc.is_paused()):
            embed = discord.Embed(description="Không có nhạc nào đang phát", color=0x2b2d31)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        if len(history) == 0:
            embed = discord.Embed(description="Không có thông tin bài hát", color=0x2b2d31)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        current_song = history[-1]
        title = current_song.get("title", "Unknown")
        artist = current_song.get("author", "")
        
        await interaction.response.defer(ephemeral=True)
        
        clean_title = re.sub(r'\(.*?\)|\[.*?\]', '', title)
        clean_title = re.sub(r'(?i)(official|music video|lyric video|audio|video)', '', clean_title)
        clean_title = " ".join(clean_title.split())
        if not clean_title:
            clean_title = title
            
        clean_artist = ""
        if artist and artist != "Unknown":
            clean_artist = re.sub(r'(?i)(official|vevo|topic|- topic)', '', artist)
            clean_artist = " ".join(clean_artist.split())
            
        search_query = f"{clean_title} {clean_artist}".strip()
        query = urllib.parse.quote(search_query)
        url = f"https://lrclib.net/api/search?q={query}"
        
        lyrics = None
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data:
                            lyrics = data[0].get("syncedLyrics") or data[0].get("plainLyrics")
            except Exception:
                pass
                
        if not lyrics:
            embed = discord.Embed(description=f"❌ Không tìm thấy lời bài hát cho **{title}**.", color=0x2b2d31)
            return await interaction.followup.send(embed=embed, ephemeral=True)
            
        if len(lyrics) > 4000:
            lyrics = lyrics[:3997] + "..."
            
        embed = discord.Embed(
            title=f"🗣️ Lyrics: {title}",
            description=lyrics,
            color=0x2b2d31
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def download(self, interaction: discord.Interaction, button: discord.ui.Button):
        source_type = str(self.track.get("source") or "").lower()
        track_url = self.track.get("url")

        try:
            duration = int(self.track.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0

        if source_type == "radio":
            return await interaction.response.send_message(
                "❌ Radio là luồng trực tiếp nên không thể tải thành MP3.",
                ephemeral=True,
            )

        if duration <= 0:
            return await interaction.response.send_message(
                "❌ Không xác định được thời lượng bài hát nên chưa thể tạo file tải xuống.",
                ephemeral=True,
            )

        if duration > MAX_DOWNLOAD_DURATION:
            return await interaction.response.send_message(
                "❌ Chỉ hỗ trợ tải bài có thời lượng tối đa 10 phút.",
                ephemeral=True,
            )

        if not track_url:
            return await interaction.response.send_message(
                "❌ Bài hát này không có địa chỉ nguồn hợp lệ.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            async with temporary_download_mp3(track_url) as mp3_path:
                file_size = os.path.getsize(mp3_path)
                upload_limit = getattr(interaction, "filesize_limit", None)

                if upload_limit and file_size > upload_limit:
                    size_mb = file_size / (1024 * 1024)
                    limit_mb = upload_limit / (1024 * 1024)
                    return await interaction.followup.send(
                        f"❌ File MP3 có dung lượng **{size_mb:.1f} MiB**, "
                        f"vượt giới hạn upload hiện tại **{limit_mb:.1f} MiB**.",
                        ephemeral=True,
                    )

                upload = discord.File(
                    mp3_path,
                    filename=safe_mp3_filename(self.track.get("title")),
                )
                try:
                    await interaction.followup.send(
                        "⬇️ MP3 của bạn đã sẵn sàng:",
                        file=upload,
                        ephemeral=True,
                    )
                finally:
                    upload.close()
        except AudioDownloadError as error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
        except Exception as error:
            logger.exception("Không thể tạo MP3 tải xuống: %s", error)
            await interaction.followup.send(
                "❌ Không thể tạo file MP3 lúc này. Hãy thử lại sau.",
                ephemeral=True,
            )

    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        result = await music_library.toggle_favorite(
            interaction.guild.id,
            interaction.user.id,
            self.track,
        )
        if result == "added":
            message = f"❤️ Đã thêm **{self.track.get('title', 'Unknown')}** vào yêu thích."
        elif result == "removed":
            message = f"💔 Đã xóa **{self.track.get('title', 'Unknown')}** khỏi yêu thích."
        else:
            message = f"❌ Bạn đã đạt giới hạn {music_library.MAX_FAVORITES} bài yêu thích."
        await interaction.followup.send(message, ephemeral=True)

    async def add_to_playlist(self, interaction: discord.Interaction, button: discord.ui.Button):
        playlists = await music_library.list_playlists(
            interaction.guild.id, interaction.user.id
        )
        if not playlists:
            return await interaction.response.send_message(
                "Bạn chưa có playlist. Hãy dùng `/playlist create` trước nhé.",
                ephemeral=True,
            )
        view = PlaylistPickerView(
            interaction.guild.id, interaction.user.id, self.track, playlists
        )
        await interaction.response.send_message(
            f"📀 Lưu **{self.track.get('title', 'Unknown')}** vào playlist nào?",
            view=view,
            ephemeral=True,
        )

    # ==========================================
    # HÀM LÀM MỚI NỘI DUNG COMPONENTS V2
    # ==========================================
    def refresh_layout(self, queue_length=None, requester_mention=None):
        # Lưu lại để lần gọi sau (vd: từ pause()) vẫn dùng đúng giá trị.
        if queue_length is not None:
            self.queue_length = queue_length
        if requester_mention is not None:
            self.requester_mention = requester_mention

        platform = self.track.get('source', 'youtube').lower()
        platform_name = platform.capitalize()
        emoji = self.get_platform_emoji(platform)
        author = _safe_text(self.track.get("author"), "Unknown Artist")
        duration = self.track.get('duration', 0)

        self.header_text.content = (
            "### 🍐 NOW PLAYING\n"
            f"**{_linked_title(self.track)}**\n"
            f"-# {author}  •  {emoji} {platform_name}  •  "
            f"⏱️ {self.format_time(duration)}"
        )

        if duration:
            current = self.get_current_time()
            bar = self.create_progress_bar(current, duration)
            self.progress_text.content = (
                f"**{self.format_time(current)}**  {bar}  "
                f"**{self.format_time(duration)}**"
            )
        else:
            self.progress_text.content = "📻 **Đang phát trực tiếp**"

        footer_parts = [
            f"🎧 Yêu cầu bởi {self.requester_mention or 'Autoplay'}",
        ]
        if self.queue_length > 0:
            footer_parts.append(f"📃 Còn {self.queue_length} bài trong hàng đợi")
        footer_parts.append("Chỉ thành viên cùng kênh thoại mới điều khiển được")
        self.footer_text.content = "-# " + "  •  ".join(footer_parts)

        return self
