import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

# Cấu hình yt-dlp tối ưu cho việc tìm kiếm
YDL_SEARCH_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "extract_flat": True, 
    "cookies": "cookies.txt", 
}

# Hàm phụ trợ để chuyển đổi giây sang định dạng MM:SS giống lệnh play của bạn
def format_duration(seconds):
    if not seconds:
        return "Unknown"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{int(h)}:{int(m):02d}:{int(s):02d}"
    return f"{int(m):02d}:{int(s):02d}"

class SearchSelect(discord.ui.Select):
    def __init__(self, entries, original_interaction):
        self.entries = entries
        self.original_interaction = original_interaction
        
        options = []
        for i, entry in enumerate(entries[:5]):
            title = entry.get("title", "Unknown Title")[:90]
            uploader = entry.get("uploader", "Unknown")[:90]
            options.append(discord.SelectOption(
                label=f"{i+1}. {title}",
                description=uploader,
                value=str(i),
                emoji="🎵" 
            ))
            
        super().__init__(placeholder="Nhấp vào đây để chọn bài hát...", max_values=1, min_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Chặn người khác bấm lén
        if interaction.user != self.original_interaction.user:
            return await interaction.response.send_message("❌ Bạn không phải là người tìm kiếm bài này!", ephemeral=True)

        # Trì hoãn tương tác để Discord không báo lỗi "Interaction Failed" khi đang xử lý
        await interaction.response.defer()

        selected_index = int(self.values[0])
        selected_track = self.entries[selected_index]
        
        # Cố gắng lấy thumbnail nếu có
        thumb = None
        if selected_track.get("thumbnails"):
            thumb = selected_track["thumbnails"][-1].get("url")

        song = {
            "title": selected_track.get("title"),
            "author": selected_track.get("uploader", "Unknown"),
            "url": selected_track.get("url") or selected_track.get("webpage_url"),
            "duration": selected_track.get("duration"),
            "thumbnail": thumb, 
            "requester": interaction.user,
            "source": "youtube"
        }

        # Xóa luôn khung tìm kiếm sau khi đã chọn bài
        try:
            await interaction.message.delete()
        except:
            pass

        from music.player import queue, play_next
        
        # 1. TỰ ĐỘNG KẾT NỐI VÀO VOICE GIỐNG LỆNH /PLAY
        vc = interaction.guild.voice_client
        if not vc or not vc.is_connected():
            vc = await interaction.user.voice.channel.connect()

        # Thêm bài hát vào hàng đợi
        queue.append(song)
        
        # 2. XỬ LÝ GIAO DIỆN VÀ PHÁT NHẠC
        if vc.is_playing() or vc.is_paused():
            # ĐÚNG YÊU CẦU: EMBED QUEUE ĐỒNG BỘ VỚI CODE CỦA BẠN
            embed = discord.Embed(
                description=f"**{song['title']}** `[{format_duration(song['duration'])}]`",
                color=0x2b2d31
            )

            embed.set_author(
                name=f"Song Added To Queue (#{len(queue)})",
                icon_url=interaction.user.display_avatar.url
            )

            if song.get("thumbnail"):
                embed.set_thumbnail(url=song["thumbnail"])

            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"▶️ Đang chuẩn bị phát: **{song['title']}**")
            await play_next(interaction.client, vc, interaction.channel)

class SearchView(discord.ui.View):
    def __init__(self, entries, original_interaction):
        super().__init__(timeout=60)
        self.original_interaction = original_interaction
        self.add_item(SearchSelect(entries, original_interaction))
        
    # NÚT HỦY TÌM KIẾM
    @discord.ui.button(label="Hủy", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.original_interaction.user:
            return await interaction.response.send_message("❌ Bạn không có quyền hủy!", ephemeral=True)
            
        await interaction.message.delete()
        await interaction.response.send_message("🚫 **Đã hủy tìm kiếm.**", ephemeral=True)


class Search(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="search", description="🔍 Tìm kiếm bài hát trên YouTube")
    @app_commands.describe(query="Tên bài hát bạn muốn tìm")
    async def search_yt(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ **Bạn phải vào kênh Voice trước mới dùng được lệnh này nhé!**", ephemeral=True)

        vc = interaction.guild.voice_client
        if vc and vc.channel.id != interaction.user.voice.channel.id:
            return await interaction.response.send_message("❌ **Mình đang phát nhạc ở kênh Voice khác mất rồi!**", ephemeral=True)

        await interaction.response.defer()
        
        loop = self.bot.loop

        def do_search():
            with yt_dlp.YoutubeDL(YDL_SEARCH_OPTIONS) as ydl:
                return ydl.extract_info(f"ytsearch5:{query}", download=False)
        
        try:
            info = await loop.run_in_executor(None, do_search)
            entries = info.get("entries", [])
        except Exception as e:
            return await interaction.followup.send(f"❌ **Lỗi khi tải dữ liệu:** {e}")
            
        if not entries:
            return await interaction.followup.send(f"⚠️ **Không tìm thấy kết quả nào cho:** `{query}`")
            
        view = SearchView(entries, interaction)
        
        embed = discord.Embed(
            title=f"🔍 Kết quả tìm kiếm: {query}",
            description="Vui lòng chọn 1 bài hát trong menu dưới đây, hoặc bấm Hủy.",
            color=0x2b2d31
        )
        
        first_thumb = None
        if entries[0].get("thumbnails"):
            first_thumb = entries[0]["thumbnails"][-1].get("url")
            
        if first_thumb:
            embed.set_thumbnail(url=first_thumb)

        await interaction.followup.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(Search(bot))