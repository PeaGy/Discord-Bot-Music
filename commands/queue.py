import discord
from discord import app_commands
from discord.ext import commands
import math

from music.state import get_guild_state

# Hàm chuyển đổi giây sang định dạng MM:SS
def format_duration(seconds):
    if not seconds:
        return "Unknown"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{int(h)}:{int(m):02d}:{int(s):02d}"
    return f"{int(m):02d}:{int(s):02d}"

class QueuePagination(discord.ui.View):
    def __init__(self, original_interaction, queue_list, current_song):
        super().__init__(timeout=120)
        self.interaction = original_interaction
        self.queue_list = list(queue_list) # Chuyển deque thành list để dễ cắt trang
        self.current_song = current_song
        self.page = 1
        self.per_page = 10 # Hiển thị 10 bài mỗi trang
        
        # Tính toán tổng số trang
        self.total_pages = math.ceil(len(self.queue_list) / self.per_page) if self.queue_list else 1
        self.update_buttons()

    def update_buttons(self):
        # Khóa nút "Trước" nếu đang ở trang 1
        self.children[0].disabled = self.page == 1
        # Khóa nút "Sau" nếu đang ở trang cuối
        self.children[1].disabled = self.page == self.total_pages

    def build_embed(self):
        embed = discord.Embed(title="🎶 Danh Sách Hàng Đợi", color=0x2b2d31)
        
        # 1. Hiển thị bài hát đang phát ở trên cùng
        if self.current_song:
            embed.add_field(
                name="▶️ Đang phát:",
                value=f"**{self.current_song.get('title', 'Unknown')}** `[{format_duration(self.current_song.get('duration'))}]`",
                inline=False
            )

        # Nếu hàng đợi trống
        if not self.queue_list:
            embed.description = "Hàng đợi đang trống."
            return embed

        # 2. Tính toán tổng thời lượng và số lượng bài hát
        total_seconds = sum(song.get("duration") or 0 for song in self.queue_list)
        embed.description = f"**{len(self.queue_list)}** bài hát trong hàng chờ | Tổng thời gian: `{format_duration(total_seconds)}`\n\n"

        # 3. Cắt danh sách theo trang hiện tại (Pagination)
        start_idx = (self.page - 1) * self.per_page
        end_idx = start_idx + self.per_page
        
        for i, song in enumerate(self.queue_list[start_idx:end_idx], start=start_idx + 1):
            title = song.get("title", "Unknown")
            duration = format_duration(song.get("duration"))
            embed.description += f"`{i}.` **{title}** `[{duration}]`\n"

        embed.set_footer(text=f"Trang {self.page}/{self.total_pages}")
        return embed

    @discord.ui.button(label="◀ Trước", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.interaction.user:
            return await interaction.response.send_message("❌ Chỉ người dùng lệnh mới có quyền lật trang!", ephemeral=True)
        
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Sau ▶", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.interaction.user:
            return await interaction.response.send_message("❌ Chỉ người dùng lệnh mới có quyền lật trang!", ephemeral=True)
        
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class QueueCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="queue", description="📜 Hiển thị danh sách bài hát đang chờ")
    async def queue_cmd(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        
        if not vc or not vc.is_connected():
            return await interaction.response.send_message("❌ **Không có nhạc nào đang phát!**", ephemeral=True)

        state = get_guild_state(interaction.guild)
        queue = state.queue
        history = state.history

        # Lấy bài hát hiện tại từ cuối danh sách lịch sử
        current_song = history[-1] if history else None
        
        if not queue and not current_song:
            return await interaction.response.send_message("❌ **Hàng đợi hiện đang trống!**", ephemeral=True)

        # Khởi tạo giao diện phân trang
        view = QueuePagination(interaction, queue, current_song)
        
        # Gửi Embed ban đầu
        await interaction.response.send_message(embed=view.build_embed(), view=view)

async def setup(bot):
    await bot.add_cog(QueueCommand(bot))
