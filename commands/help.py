import discord
from discord import app_commands
from discord.ext import commands

class HelpDropdown(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.select(
        placeholder="Select to view the commands",
        options=[
            discord.SelectOption(label="Information", emoji="📢", value="info"),
            discord.SelectOption(label="Commands", emoji="❗", value="commands"),
            discord.SelectOption(label="Art & AI", emoji="🎨", value="art_ai"),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        value = select.values[0]
        if value == "info":
            embed = discord.Embed(
                description=(
                    "📢 **Information**\n\n"
                    "Tracen Jukebox - Máy phát nhạc xịn xò cho học viện Tracen.\n\n"
                    "✨ **Tính năng nổi bật:**\n"
                    "💾 **Intelligent Cache:** Tự động lưu nhạc ngắn vào ổ cứng, phát lại tức thì.\n"
                    "📡 **Smart Streaming:** Tự động phát trực tiếp cho Radio & nhạc\n"
                    "🎵 **Multi-Platform:** Hỗ trợ YouTube, Spotify, SoundCloud.\n\n"
                    "Original code by Eva Music Bot, inspired by Lara Bot. Thanks to Ryuz-V.\n\n"
                    "Created by **PeaGy**"
                )
            )
        elif value == "commands":
            embed = discord.Embed(
                description=(
                    "❗ **Commands**\n\n"
                    "```ansi\n"
                    "\u001b[32mMusic Commands:\u001b[0m\n"
                    "```\n"
                    "`/play` : Phát nhạc\n"
                    "`/search` : Tìm kiếm nhạc\n"
                    "`/pause` : Tạm dừng\n"
                    "`/resume` : Tiếp tục phát\n"
                    "`/skip` : Bỏ qua bài\n"
                    "`/stop` : Dừng nhạc\n"
                    "`/previous` : Bài trước đó\n"
                    "`/queue` : Xem hàng đợi\n"
                    "`/loop` : Lặp lại nhạc\n"
                    "`/autoplay` : Bật/Tắt tự động phát\n"
                    "`/radio` : Phát Radio\n"
                    "`/lyric` : Xem lời bài hát\n"
                    "`/247` : Chế độ 24/7\n"
                    "`/connect` : Kết nối Voice\n"
                    "`/leave` : Rời Voice\n"
                    "`/latency` : Kiểm tra độ trễ (Ping)\n"
                )
            )
        elif value == "art_ai":
            embed = discord.Embed(
                description=(
                    "🎨 **Art & AI Features**\n\n"
                    "```ansi\n"
                    "\u001b[35mDanbooru Art System:\u001b[0m\n"
                    "```\n"
                    "`/art` : Tìm ảnh anime (SFW - An toàn)\n"
                    "`/wallpaper` : Ảnh chất lượng siêu cao làm hình nền\n"
                    "`/artecchi` : Tìm ảnh gợi cảm (Ecchi) - **Chỉ kênh NSFW**\n"
                    "`/artnsfw` : Tìm ảnh 18+ hạng nặng - **Chỉ kênh NSFW**\n"
                    "`/artinfo` : Tra cứu chi tiết nghệ sĩ/tags theo ID bài post\n\n"
                    "```ansi\n"
                    "\u001b[36mAI Chat Assistant (Peto):\u001b[0m\n"
                    "```\n"
                    "🗣️ **Không cần dùng lệnh!** Bạn chỉ cần **Tag bot (@Peto)** hoặc **Reply tin nhắn** của bot để bắt đầu trò chuyện.\n\n"
                    "💡 *Tính năng tự động:* Peto có thể tự tra cứu thông tin thời tiết, thời sự trên web, hoặc tự động bắt lệnh điều khiển nhạc (mở nhạc, skip bài...) ngay trong lúc chat với bạn!\n\n"
                    "```ansi\n"
                    "\u001b[33mAI Memory Control:\u001b[0m\n"
                    "```\n"
                    "`/resetmemory` : 🧹 Xoá lịch sử chat của bạn với AI[cite: 8]\n"
                    "`/resetmemoryall` : 🧹 [Admin] Xoá trí nhớ chat của mọi người TRONG SERVER NÀY[cite: 8]\n"
                    "`/resetmemoryglobal` : 🧹 [Chỉ dev] Xoá TOÀN BỘ trí nhớ, mọi server, mọi người[cite: 8]\n"
                )
            )
        
        embed.set_image(url="https://cdn.discordapp.com/attachments/1461031433689759826/1470602546371493950/uma-musume-agnes-tachyon.gif")
        await interaction.response.edit_message(embed=embed, view=self)


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @app_commands.command(name="help", description="Show Help Panel")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🍐 Tracen Jukebox Help Panel",
            description=(
                "**🗣️❓ Máy phát nhạc Tracen là gì?**\n"
                "Tracen Jukebox là bot phát nhạc hiện đại được thiết kế để mang đến trải nghiệm âm nhạc tuyệt vời nhất "
                "chất lượng âm nhạc cao cùng với các tính năng thông minh như autoplay, "
                "24/7 mode, và multi-platform support gồm **Spotify, YouTube, "
                "and SoundCloud**.\n\n"
                "**⭐ Available Categories**\n"
                "ℹ️ **:** Information\n"
                "❗ **:** Commands\n"
                "🎨 **:** Art & AI\n"
            )
        )
        embed.set_image(url="https://cdn.discordapp.com/attachments/1461031433689759826/1470602546371493950/uma-musume-agnes-tachyon.gif")
        await interaction.response.send_message(embed=embed, view=HelpDropdown())

async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))