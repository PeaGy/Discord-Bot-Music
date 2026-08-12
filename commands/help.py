import discord
from discord import app_commands
from discord.ext import commands


HELP_IMAGE_URL = (
    "https://cdn.discordapp.com/attachments/1461031433689759826/"
    "1470602546371493950/uma-musume-agnes-tachyon.gif"
)
EMBED_COLOR = 0x2B2D31


def build_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=EMBED_COLOR,
    )
    embed.set_image(url=HELP_IMAGE_URL)
    embed.set_footer(text="Tracen Jukebox • Chọn danh mục bên dưới để xem thêm")
    return embed


class HelpDropdown(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Chọn một danh mục trợ giúp",
        options=[
            discord.SelectOption(
                label="Giới thiệu",
                description="Tổng quan về Tracen Jukebox",
                emoji="📢",
                value="info",
            ),
            discord.SelectOption(
                label="Lệnh âm nhạc",
                description="Các lệnh phát nhạc và điều khiển voice",
                emoji="🎵",
                value="commands",
            ),
            discord.SelectOption(
                label="Art & AI",
                description="Danbooru, Grok và bộ nhớ Peto",
                emoji="🎨",
                value="art_ai",
            ),
            discord.SelectOption(
                label="Tải media",
                description="Tải TikTok, YouTube và X",
                emoji="📥",
                value="media",
            ),
        ],
    )
    async def select_callback(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ):
        value = select.values[0]

        if value == "info":
            embed = build_embed(
                "📢 Giới thiệu Tracen Jukebox",
                (
                    "Bot âm nhạc, hình ảnh và trò chuyện AI dành cho học viện Tracen.\n\n"
                    "✨ **Tính năng nổi bật**\n"
                    "💾 **Intelligent Cache:** Lưu nhạc ngắn trên ổ đĩa và tải trước bài kế tiếp.\n"
                    "🎚️ **Loudness Normalization:** Chuẩn hóa âm lượng để các bài nghe đồng đều hơn.\n"
                    "📡 **Smart Streaming:** Stream trực tiếp radio và bài dài hơn 10 phút.\n"
                    "🎵 **Multi-Platform:** Hỗ trợ YouTube, Spotify track và SoundCloud.\n"
                    "🏫 **Multi-Server:** Mỗi server có queue, lịch sử và chế độ phát riêng.\n"
                    "❤️ **Thư viện cá nhân:** Lưu favorites, playlist và lịch sử nghe bằng SQLite.\n"
                    "🤖 **Peto AI:** Trò chuyện bằng Grok, xem ảnh, tìm web qua Tavily và ghi nhớ bằng SQLite.\n"
                    "📚 **Study Mode:** Gợi ý, giải chi tiết, kiểm tra đáp án và xuất lời giải PNG.\n"
                    "🎨 **Danbooru:** Tìm anime art, wallpaper và xem thông tin post.\n\n"
                    "Original code by Eva Music Bot, inspired by Lara Bot. "
                    "Thanks to Ryuz-V.\n\n"
                    "Created by **PeaGy**"
                ),
            )

        elif value == "commands":
            embed = build_embed(
                "🎵 Lệnh âm nhạc & voice",
                (
                    "**Phát và quản lý hàng đợi**\n"
                    "`/play <query>` — Phát nhạc từ từ khóa hoặc URL\n"
                    "`/search <query>` — Tìm 5 kết quả YouTube để lựa chọn\n"
                    "`/queue` — Xem bài hiện tại và hàng đợi\n"
                    "`/playnext <query>` — Ưu tiên phát bài kế tiếp\n"
                    "`/remove` · `/move` · `/shuffle` · `/clear` — Quản lý hàng đợi\n"
                    "`/favorite` · `/favorites` — Lưu hoặc xem nhạc yêu thích\n"
                    "`/recent` — Xem lịch sử nghe gần đây của server\n"
                    "`/previous` — Quay lại bài trước\n"
                    "`/next` — Bỏ qua bài hiện tại\n"
                    "`/stop` — Dừng nhạc, xóa hàng đợi và rời voice\n\n"
                    "**Điều khiển phát nhạc**\n"
                    "`/pause` — Tạm dừng\n"
                    "`/resume` — Tiếp tục phát\n"
                    "`/loop` — Chuyển trạng thái lặp\n"
                    "`/autoplay` — Bật hoặc tắt phát bài liên quan\n"
                    "`/lyric` — Xem trước, mở đầy đủ hoặc dịch lời bài hát\n"
                    "`/stats` · `/wrapped` — Thống kê và tổng kết nghe nhạc\n"
                    "`/radio` — Chọn và phát radio internet\n\n"
                    "**Playlist cá nhân**\n"
                    "`/playlist create <name>` — Tạo playlist\n"
                    "`/playlist list` — Xem các playlist\n"
                    "`/playlist add <name> [query]` — Thêm bài đang phát hoặc link/tên bài\n"
                    "`/playlist show <name>` — Xem nội dung playlist\n"
                    "`/playlist play <name> [shuffle]` — Phát playlist, có xáo thông minh\n"
                    "`/playlist savequeue <name>` — Lưu hàng đợi vào playlist\n"
                    "`/playlist share` · `/playlist clone` — Chia sẻ và sao chép\n"
                    "`/playlist import` — Nhập playlist YouTube\n"
                    "`/playlist delete <name>` — Xóa playlist\n\n"
                    "**Kết nối và tiện ích**\n"
                    "`/connect` — Kết nối vào voice channel\n"
                    "`/leave` — Rời voice channel\n"
                    "`/247` — Bật hoặc tắt chế độ ở lại voice\n"
                    "`/latency` — Kiểm tra độ trễ Discord\n"
                    "`/help` — Mở bảng trợ giúp này\n\n"
                    "⬇️ **Nút Tải xuống:** Gửi riêng file MP3 cho bài tối đa 10 phút; "
                    "không áp dụng cho radio hoặc file vượt giới hạn upload.\n\n"
                    "❤️ **Nút Yêu thích:** Thêm hoặc xóa bài hiện tại khỏi favorites của bạn.\n"
                    "➕ **Nút Playlist:** Chọn playlist và lưu bài đang phát bằng menu riêng tư.\n\n"
                    "🧩 **Music Panel V2:** Ảnh bìa, thông tin bài, tiến trình và hai hàng nút "
                    "được sắp xếp bằng giao diện Components V2 của Discord.\n\n"
                    "-# Nút Loop và lệnh /loop cùng chuyển theo chu kỳ Off → Track → Queue."
                ),
            )

        elif value == "art_ai":
            embed = build_embed(
                "🎨 Art & AI",
                (
                    "**Danbooru Art System**\n"
                    "`/art [tags]` — Tìm ảnh anime SFW\n"
                    "`/wallpaper [hướng] [tags]` — Tìm wallpaper chất lượng cao\n"
                    "`/artecchi [tags]` — Tìm ảnh ecchi, chỉ trong kênh NSFW\n"
                    "`/artnsfw [tags]` — Tìm ảnh explicit, chỉ trong kênh NSFW\n"
                    "`/artinfo <id>` — Xem artist, nguồn, score và tags của post\n\n"
                    "**Peto AI • Grok by xAI**\n"
                    "Mention **@Peto** hoặc reply tin nhắn của bot để trò chuyện, "
                    "không cần slash command. Dùng `/private` để Peto mở DM; trong "
                    "DM bạn chỉ cần nhắn bình thường, không cần mention.\n\n"
                    "Peto có thể xem ảnh đính kèm, tìm thông tin mới trên web qua Tavily, "
                    "phát nhạc, bỏ qua bài và duy trì trí nhớ hội thoại bằng SQLite. "
                    "Trí nhớ dài hạn của mỗi người được đồng bộ giữa DM và mọi server, "
                    "nhưng không chia sẻ cho người khác. Bạn cũng có thể nhờ Peto tìm "
                    "fanart SFW trực tiếp từ Danbooru.\n\n"
                    "Peto hiểu tối đa 8 tin trong chuỗi reply. Bạn có thể hỏi "
                    "`mọi người đang bàn gì?` để tóm tắt tối đa 40 tin gần nhất "
                    "trong đúng kênh hiện tại. Câu trả lời dài được tự chia tối đa "
                    "3 tin Discord; dài hơn nữa sẽ được gửi trọn vẹn bằng file "
                    "`.txt` thân thiện với điện thoại.\n\n"
                    "Nhấp phải một tin nhắn → **Apps → Hỏi Peto** để giải thích, "
                    "dịch, tóm tắt hoặc soạn câu trả lời. Peto cũng có thể đọc link "
                    "công khai được gửi kèm yêu cầu.\n\n"
                    "Câu hỏi thực tế có nút **Kiểm tra nguồn**. Nhấp phải tin có ảnh "
                    "→ **Apps → Tạo sticker & emoji** để nhận PNG 320px và 128px.\n\n"
                    "`/sticker image:<ảnh>` · `/emoji image:<ảnh>` — Tạo file trực tiếp. "
                    "Bạn cũng có thể reply ảnh và nói `@Peto tạo sticker`; thao tác "
                    "này xử lý cục bộ, không gọi AI tạo ảnh.\n\n"
                    "**Study Mode**\n"
                    "Mention Peto kèm đề bài hoặc ảnh và nói `giải bài này`. "
                    "Peto sẽ hiện các nút **Gợi ý**, **Giải chi tiết**, "
                    "**Kiểm tra đáp án**, **Chép đề** và **Xuất PNG**. Có thể gửi "
                    "nhiều trang bằng chuỗi reply. Chỉ người gửi đề sử dụng "
                    "được phiên này; nút tự khóa sau 15 phút. Bài tập được giải "
                    "từ đúng dữ kiện đã gửi, không tìm đáp án gần giống trên web; "
                    "nếu đề phụ thuộc hình còn thiếu, Peto sẽ yêu cầu gửi ảnh.\n\n"
                    "**Điều khiển bộ nhớ AI**\n"
                    "`/private` — Mở cuộc trò chuyện riêng qua DM\n"
                    "`/andanh` — Bật/tắt Ẩn danh tại DM hoặc server hiện tại\n"
                    "`/resetmemory` — Xóa toàn bộ trí nhớ đã lưu của chính bạn\n"
                    "`/resetmemoryall` — Admin xóa lịch sử chat trong server hiện tại\n"
                    "`/resetmemoryglobal` — Chủ bot xóa toàn bộ trí nhớ AI"
                ),
            )

        else:
            embed = build_embed(
                "📥 Universal Media Downloader",
                (
                    "Dùng `/download link:<URL>` để tạo embed có tiêu đề, thumbnail, tác giả, "
                    "thời lượng, định dạng và nút tải. Bot không tự quét các link được gửi "
                    "trong chat. Toàn bộ panel và file tải đều **Only Visible to you**; "
                    "file chỉ được xử lý khi bạn bấm nút.\n\n"
                    "**Quy tắc định dạng**\n"
                    "🎵 **YouTube** — Tải MP3\n"
                    "🎬 **TikTok video** — Tải MP4 không watermark\n"
                    "🖼️ **TikTok photo** — Tải toàn bộ ảnh gốc theo từng file\n"
                    "🎬 **X / Twitter** — Tải MP4\n\n"
                    "TikTok dùng `yt-dlp` làm nguồn chính và tự chuyển sang TikWM "
                    "khi gặp challenge/rehydration. Link chỉ được gửi tới dịch vụ "
                    "fallback khi nguồn chính thất bại.\n\n"
                    "Chỉ xử lý link video cụ thể dài tối đa 10 phút. Một bài TikTok photo "
                    "được tải tối đa 35 ảnh; bot tự chia thành nhiều lượt gửi, mỗi lượt tối đa "
                    "10 file và vẫn tuân theo giới hạn upload của Discord. Playlist, "
                    "livestream, nội dung riêng tư và file vượt giới hạn upload "
                    "hiện tại của Discord sẽ bị từ chối. Mỗi người chỉ có một lượt "
                    "tải đang chạy; nút tải hết hạn sau 10 phút và file tạm được xóa "
                    "ngay sau khi gửi.\n\n"
                    "-# Facebook và Instagram hiện không nằm trong phạm vi hỗ trợ."
                ),
            )

        await interaction.response.edit_message(embed=embed, view=self)


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="help",
        description="Mở bảng hướng dẫn sử dụng Tracen Jukebox",
    )
    async def help(self, interaction: discord.Interaction):
        embed = build_embed(
            "🍐 Tracen Jukebox Help Panel",
            (
                "Tracen Jukebox kết hợp phát nhạc chất lượng cao, radio internet, "
                "anime art và trợ lý AI Peto sử dụng Grok của xAI.\n\n"
                "**Danh mục trợ giúp**\n"
                "📢 **Giới thiệu** — Tổng quan tính năng và công nghệ\n"
                "🎵 **Lệnh âm nhạc** — Phát nhạc, hàng đợi và voice\n"
                "🎨 **Art & AI** — Danbooru, Peto và bộ nhớ hội thoại\n"
                "📥 **Tải media** — TikTok video/ảnh, YouTube MP3 và X MP4\n\n"
                "Chọn một danh mục trong menu bên dưới để xem chi tiết."
            ),
        )
        await interaction.response.send_message(embed=embed, view=HelpDropdown())


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
