import discord
import aiohttp
import urllib.parse
import re
import time

class MusicControl(discord.ui.View):
    def __init__(self, vc, track, current_time=0, queue_length=0, requester_mention=None):
        super().__init__(timeout=None)
        self.vc = vc
        self.track = track
        self.current_time = current_time
        self.queue_length = queue_length
        self.requester_mention = requester_mention
        # ==========================================
        # FIX LỖI 1: ĐỒNG BỘ TRẠNG THÁI NÚT BẤM KHI RENDER LẠI UI
        # ==========================================
        # Khởi tạo biến loop_mode cho vc nếu chưa có
        if not hasattr(self.vc, "loop_mode"):
            self.vc.loop_mode = "off"

        # Import autoplay_guilds để check trạng thái autoplay
        try:
            from music.player import autoplay_guilds
            guild_id = self.vc.guild.id
        except ImportError:
            autoplay_guilds = set()
            guild_id = None

        # Duyệt qua các nút và cập nhật hiển thị theo đúng biến hệ thống
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                # Đồng bộ nút Loop
                if child.label and "Loop" in child.label:
                    if self.vc.loop_mode == "track":
                        child.label = "Loop Track"
                        child.emoji = "🔂"
                        child.style = discord.ButtonStyle.success
                    elif self.vc.loop_mode == "queue":
                        child.label = "Loop Queue"
                        child.emoji = "🔁"
                        child.style = discord.ButtonStyle.success
                    else:
                        child.label = "Loop Off"
                        child.emoji = "➡️"
                        child.style = discord.ButtonStyle.secondary
                
                # Đồng bộ nút Autoplay
                if child.label and "Autoplay" in child.label and guild_id:
                    if guild_id in autoplay_guilds:
                        child.label = "Autoplay On"
                        child.style = discord.ButtonStyle.success
                    else:
                        child.label = "Autoplay Off"
                        child.style = discord.ButtonStyle.secondary
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
    @discord.ui.button(label="Back", emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        from music.player import history, queue, play_next

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

    @discord.ui.button(label="Pause", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
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

        # Vẽ lại embed để thanh tiến trình đúng tại thời điểm pause/resume này
        embed = self.generate_embed(queue_length=self.queue_length, requester_mention=self.requester_mention)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.skip_request = True
            self.vc.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        from music.player import queue, now_playing_messages
        msg = now_playing_messages.pop(interaction.guild.id, None)
        queue.clear()
        
        for child in self.children:
            child.disabled = True

        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.stop_request = True
            self.vc.stop()
        if self.vc.is_connected():
            await self.vc.disconnect()
            
        if msg:
            embed = discord.Embed(description="⏹️ **Đã cút**", color=0xFF6B6B)
            try:
                await msg.edit(embed=embed, view=self)
            except: pass
        await interaction.response.defer()

    # ==========================================
    # ROW 2: TÍNH NĂNG BỔ SUNG
    # ==========================================
    @discord.ui.button(label="Loop Off", emoji="➡️", style=discord.ButtonStyle.secondary, row=1)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        
        # FIX LỖI 2: Lưu trạng thái vào self.vc thay vì bot
        if not hasattr(self.vc, "loop_mode"):
            self.vc.loop_mode = "off"

        # Chu kỳ: off -> track -> queue -> off
        if self.vc.loop_mode == "off":
            self.vc.loop_mode = "track"
            button.label = "Loop Track"
            button.emoji = "🔂"
            button.style = discord.ButtonStyle.success
            msg = "🔂 **Lặp lại bài hiện tại**"
        elif self.vc.loop_mode == "track":
            self.vc.loop_mode = "queue"
            button.label = "Loop Queue"
            button.emoji = "🔁"
            button.style = discord.ButtonStyle.success
            msg = "🔁 **Lặp lại toàn bộ danh sách (Queue)**"
        else:
            self.vc.loop_mode = "off"
            button.label = "Loop Off"
            button.emoji = "➡️"
            button.style = discord.ButtonStyle.secondary
            msg = "➡️ **Đã tắt chế độ lặp**"

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Autoplay Off", emoji="🔀", style=discord.ButtonStyle.secondary, row=1)
    async def autoplay_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        
        # IMPORT thêm queue và handle_autoplay để xử lý tiên tri
        from music.player import autoplay_guilds, queue, handle_autoplay
        import asyncio

        guild_id = interaction.guild.id

        if guild_id in autoplay_guilds:
            autoplay_guilds.remove(guild_id)
            button.label = "Autoplay Off"
            button.style = discord.ButtonStyle.secondary
            msg = "❌ **Tắt Autoplay**"
        else:
            autoplay_guilds.add(guild_id)
            button.label = "Autoplay On"
            button.style = discord.ButtonStyle.success
            msg = "✅ **Bật Autoplay**"
            
            # ==============================
            # ⚡ KÍCH HOẠT TIÊN TRI NGAY LẬP TỨC
            # ==============================
            # Nếu hàng đợi trống và bot đang hát -> Gọi hàm tiên tri ngầm luôn!
            if not queue and (self.vc.is_playing() or self.vc.is_paused()):
                bot = interaction.client
                # self.track chính là bài hát đang phát hiện tại
                asyncio.create_task(
                    handle_autoplay(bot, self.vc, interaction.channel, self.track, guild_id, trigger_play=False)
                )

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Lyric", emoji="📝", style=discord.ButtonStyle.secondary, row=1)
    async def lyric(self, interaction: discord.Interaction, button: discord.ui.Button):
        from music.player import history

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

    # ==========================================
    # HÀM RENDER EMBED CHÍNH
    # ==========================================
    def generate_embed(self, queue_length=None, requester_mention=None):
        # Lưu lại để lần gọi sau (vd: từ pause()) không cần truyền lại vẫn dùng đúng giá trị
        if queue_length is not None:
            self.queue_length = queue_length
        if requester_mention is not None:
            self.requester_mention = requester_mention

        embed = discord.Embed(
            title="Now Playing 🍐",
            description=f"**[{self.track.get('title', 'Unknown Track')}]({self.track.get('url', '')})**",
            color=0x2b2d31 # Màu đen nhạt
        )

        # 1. Artist Field
        embed.add_field(
            name="Artist",
            value=self.track.get('author', 'Unknown Artist'),
            inline=True
        )

        # 2. Duration Field
        embed.add_field(
            name="Duration",
            value=self.format_time(self.track.get('duration', 0)),
            inline=True
        )

        # 3. Platform Field
        platform = self.track.get('source', 'youtube').lower()
        platform_name = platform.capitalize()
        emoji = self.get_platform_emoji(platform)
        embed.add_field(
            name="Platform",
            value=f"{emoji} {platform_name}",
            inline=True
        )

        # 4. Progress Field tính on-demand tại thời điểm embed được vẽ
        duration = self.track.get('duration', 0)
        if duration:
            current = self.get_current_time()
            bar = self.create_progress_bar(current, duration)
            embed.add_field(
                name="Progress",
                value=f"{self.format_time(current)} / {self.format_time(duration)}\n{bar}",
                inline=False
            )

        # 6. Requested By
        if self.requester_mention:
            embed.add_field(
                name="Requested by",
                value=self.requester_mention,
                inline=True
            )

        # 7. Thumbnail
        if self.track.get('thumbnail'):
            embed.set_thumbnail(url=self.track.get('thumbnail'))

        # 8. Footer Queue Info
        footer_text = "Only chuds can control the panel."
        if self.queue_length > 0:
            footer_text += f" • {self.queue_length} more songs in queue"
        embed.set_footer(text=footer_text)

        return embed