import discord
from discord import app_commands
from discord.ext import commands


HELP_IMAGE_URL = (
    "https://cdn.discordapp.com/attachments/1461031433689759826/"
    "1470602546371493950/uma-musume-agnes-tachyon.gif"
)
EMBED_COLOR = 0x2B2D31
HELP_PAGE_DESCRIPTION_LIMIT = 3900


def split_help_description(
    description: str,
    limit: int = HELP_PAGE_DESCRIPTION_LIMIT,
) -> list[str]:
    """Chia help dài thành nhiều trang mà không vượt giới hạn embed Discord."""
    if len(description) <= limit:
        return [description]

    pages: list[str] = []
    current = ""
    for paragraph in description.split("\n\n"):
        pending = paragraph
        prefix = "\n\n" if current else ""
        if len(current) + len(prefix) + len(pending) <= limit:
            current += prefix + pending
            continue

        if current:
            pages.append(current)
            current = ""

        while len(pending) > limit:
            split_at = pending.rfind("\n", 0, limit + 1)
            if split_at <= 0:
                split_at = limit
            pages.append(pending[:split_at].rstrip())
            pending = pending[split_at:].lstrip("\n")
        current = pending

    if current:
        pages.append(current)
    return pages or [description[:limit]]


def build_embed(
    title: str,
    description: str,
    *,
    page: int = 0,
    page_count: int = 1,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=EMBED_COLOR,
    )
    embed.set_image(url=HELP_IMAGE_URL)
    footer = "Tracen Jukebox • Chọn danh mục bên dưới để xem thêm"
    if page_count > 1:
        footer += f" • Trang {page + 1}/{page_count}"
    embed.set_footer(text=footer)
    return embed


class HelpDropdown(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.current_title = ""
        self.current_pages: list[str] = []
        self.current_page = 0
        self.previous_page.disabled = True
        self.next_page.disabled = True

    def _current_embed(self) -> discord.Embed:
        return build_embed(
            self.current_title,
            self.current_pages[self.current_page],
            page=self.current_page,
            page_count=len(self.current_pages),
        )

    def _update_page_buttons(self) -> None:
        self.previous_page.disabled = self.current_page <= 0
        self.next_page.disabled = self.current_page >= len(self.current_pages) - 1

    async def _show_embed(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
    ) -> None:
        self.current_title = str(embed.title or "Tracen Jukebox Help")
        self.current_pages = split_help_description(str(embed.description or ""))
        self.current_page = 0
        self._update_page_buttons()
        await interaction.response.edit_message(embed=self._current_embed(), view=self)

    @discord.ui.button(
        label="Trước",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        disabled=True,
        row=1,
    )
    async def previous_page(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        if self.current_pages and self.current_page > 0:
            self.current_page -= 1
        self._update_page_buttons()
        await interaction.response.edit_message(embed=self._current_embed(), view=self)

    @discord.ui.button(
        label="Sau",
        emoji="▶️",
        style=discord.ButtonStyle.secondary,
        disabled=True,
        row=1,
    )
    async def next_page(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        if self.current_pages and self.current_page < len(self.current_pages) - 1:
            self.current_page += 1
        self._update_page_buttons()
        await interaction.response.edit_message(embed=self._current_embed(), view=self)

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
                label="Game",
                description="Limbus, Daily Reset và Gift Code",
                emoji="🎮",
                value="games",
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
                    "Bot âm nhạc, hình ảnh và trò chuyện AI dành cho server của bạn.\n\n"
                    "✨ **Tính năng nổi bật**\n"
                    "💾 **Intelligent Cache:** Lưu nhạc ngắn trên ổ đĩa và tải trước bài kế tiếp.\n"
                    "🎚️ **Loudness Normalization:** Chuẩn hóa âm lượng để các bài nghe đồng đều hơn.\n"
                    "📡 **Smart Streaming:** Stream trực tiếp radio và bài dài hơn 10 phút.\n"
                    "🎵 **Multi-Platform:** Hỗ trợ YouTube, Spotify track và SoundCloud.\n"
                    "🏫 **Multi-Server:** Mỗi server có queue, lịch sử và chế độ phát riêng.\n"
                    "❤️ **Thư viện cá nhân:** Lưu favorites, playlist và lịch sử nghe.\n"
                    "🤖 **Peto AI:** Trò chuyện, xem ảnh, xem video, tìm web và ghi nhớ những gì bạn nói.\n"
                    "📚 **Study Mode:** Gợi ý, chép đề và xuất lời giải PNG.\n"
                    "🎨 **Danbooru:** Tìm anime art, wallpaper và xem thông tin post.\n"
                    "👋 **Welcome:** Chào thành viên mới. \n\n"
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
                    "**Tìm nguồn ảnh**\n"
                    "`/saucy image:<ảnh>` — Mở panel riêng tư gồm Google Lens, "
                    "SauceNAO, IQDB, TinEye, Yandex và Bing. Hoặc nhấp phải tin có "
                    "ảnh → **Apps → Tìm nguồn ảnh**. Bot chỉ tạo liên kết tìm kiếm, "
                    "không dùng API key và không tự đọc kết quả.\n\n"
                    "**Peto AI • Grok by xAI**\n"
                    "Mention **@Peto** hoặc reply tin nhắn của bot để trò chuyện, "
                    "không cần slash command. Dùng `/private` để Peto mở DM; trong "
                    "DM bạn chỉ cần nhắn bình thường, không cần mention.\n\n"
                    "Peto có thể xem ảnh và video ngắn đính kèm, tìm thông tin mới trên web, "
                    "phát nhạc, bỏ qua bài và duy trì trí nhớ hội thoại. "
                    "Trí nhớ dài hạn của mỗi người được đồng bộ giữa DM và mọi server, "
                    "nhưng không chia sẻ cho người khác. Bạn cũng có thể nhờ Peto tìm "
                    "fanart SFW trực tiếp từ Danbooru.\n\n"
                    "Với video MP4/MOV/WebM/MKV tối đa 2 phút, hãy đính kèm hoặc "
                    "reply clip rồi hỏi `video này nói gì?` / `tóm tắt clip`. Peto "
                    "sẽ đọc các khung hình theo thời gian và phiên âm lời nói; bot "
                    "không tự phân tích mọi video được gửi trong kênh.\n\n"
                    "Peto hiểu tối đa 8 tin trong chuỗi reply. Bạn có thể hỏi "
                    "`mọi người đang bàn gì?` để tóm tắt tối đa 40 tin gần nhất "
                    "trong đúng kênh hiện tại. Câu trả lời dài được tự chia tối đa "
                    "3 tin Discord; dài hơn nữa sẽ được gửi trọn vẹn bằng file "
                    "`.txt` thân thiện với điện thoại.\n\n"
                    "Nhấp phải một tin nhắn → **Apps → Hỏi Peto** để giải thích, "
                    "dịch, tóm tắt hoặc soạn câu trả lời. Peto cũng có thể đọc link "
                    "công khai được gửi kèm yêu cầu.\n\n"
                    "Muốn biết nguồn của một câu trả lời, hãy hỏi trực tiếp Peto. "
                    "Nhấp phải tin có ảnh → **Apps → Tạo sticker & emoji** để nhận "
                    "PNG 320px và 128px.\n\n"
                    "`/sticker image:<ảnh>` · `/emoji image:<ảnh>` — Tạo file trực tiếp. "
                    "Bạn cũng có thể reply ảnh và nói `@Peto tạo sticker`; thao tác "
                    "này xử lý cục bộ, không gọi AI tạo ảnh.\n\n"
                    "**Study Mode**\n"
                    "Mention Peto kèm đề bài hoặc ảnh và nói `giải bài này`. "
                    "Peto sẽ hiện các nút **Gợi ý**, **Chép đề** và **Xuất PNG**. "
                    "Có thể gửi "
                    "nhiều trang bằng chuỗi reply. Chỉ người gửi đề sử dụng "
                    "được phiên này; nút tự khóa sau 15 phút. Bài tập được giải "
                    "từ đúng dữ kiện đã gửi, không tìm đáp án gần giống trên web; "
                    "nếu đề phụ thuộc hình còn thiếu, Peto sẽ yêu cầu gửi ảnh.\n\n"
                    "**Điều khiển bộ nhớ AI**\n"
                    "Trí nhớ cá nhân đi theo Discord user ID giữa DM và mọi server. "
                    "Nói `hãy nhớ...` hoặc `chốt từ giờ...` để Peto lưu ngay; hỏi "
                    "`Peto còn nhớ...?` để tìm sâu trong toàn bộ lịch sử của bạn.\n"
                    "`/private` — Mở cuộc trò chuyện riêng qua DM\n"
                    "`/andanh` — Bật/tắt Ẩn danh tại DM hoặc server hiện tại\n"
                    "`/resetmemory` — Xóa toàn bộ trí nhớ đã lưu của chính bạn\n"
                    "`/resetmemoryall` — Admin xóa lịch sử chat trong server hiện tại\n"
                    "`/resetmemoryglobal` — Chủ bot xóa toàn bộ trí nhớ AI\n"
                    "`/blacklist` · `/unblacklist` — Chủ bot chặn hoặc mở lại quyền chat với Peto"
                ),
            )

        elif value == "games":
            embed = build_embed(
                "🎮 Tiện ích game",
                (
                    "**Limbus Company Wiki & Kit**\n"
                    "Peto có kho wiki.gg tự đồng bộ riêng để tra Identity, E.G.O., "
                    "skill/passive, status, enemy, lore, Mirror Dungeon và team building. "
                    "Câu trả lời ưu tiên dữ liệu wiki, có link nguồn và nói rõ khi dữ liệu "
                    "chưa đủ thay vì đoán.\n\n"
                    "Hỏi `full skill` hoặc `full kit` để nhận các card skill/passive có màu "
                    "theo Sin Affinity cùng emoji damage type/status. Hỏi riêng `S1`, `S2`, "
                    "`S3` hoặc `Defense` để chỉ nhận một card. Identity/E.G.O có artwork wiki "
                    "sẽ hiện thumbnail tự động.\n"
                    "`/limbusasset preview <tên>` — Xem artwork Identity/E.G.O\n"
                    "`/limbusasset status` — Xem trạng thái kho artwork\n\n"
                    "**Limbus Gacha**\n"
                    "`/gacha [pulls]` — Mô phỏng Standard Extraction ×1/×10 bằng roster "
                    "và artwork đã đồng bộ. Lượt thứ 10 bảo đảm 2★ trở lên; không tiêu "
                    "Lunacy và không lưu inventory.\n\n"
                    "**Project Moon YouTube**\n"
                    "Khi được chủ bot cấu hình, Peto theo dõi kênh YouTube chính thức của "
                    "Project Moon và đăng video Limbus mới mà không gửi trùng sau khi restart.\n"
                    "Chủ bot: `/projectmoon status`, `/projectmoon preview`, "
                    "`/projectmoon test` và `/projectmoon check`.\n\n"
                    "**Daily Reset**\n"
                    "`/dailyreset next <game>` — Xem lần reset tiếp theo theo giờ của bạn\n"
                    "`/dailyreset subscribe <game>` — Nhận cảnh báo/reset qua DM\n"
                    "`/dailyreset unsubscribe <game>` — Tắt DM của game\n"
                    "`/dailyreset subscriptions` — Xem các game đã đăng ký\n"
                    "Thông báo công khai có checklist tiếng Việt và nút đăng ký DM. Hiện "
                    "hỗ trợ NIKKE, Blue Archive, Trickcal, Chaos Zero Nightmare, Limbus "
                    "Company và Brown Dust 2. Riêng Limbus chỉ nhắc Mirror Dungeon reset "
                    "vào Thứ Năm hằng tuần.\n\n"
                    "**Gift Code**\n"
                    "`/coupon codes <game>` — Xem code chưa dùng còn hoạt động\n"
                    "`/coupon subscribe <game> [account_id] [auto_redeem]` — Bật DM; "
                    "Brown Dust 2 có thể tự nhập code bằng nickname\n"
                    "`/coupon unsubscribe <game>` — Xóa đăng ký và hồ sơ game đó\n"
                    "`/coupon subscriptions` · `/coupon history` — Xem hồ sơ và kết quả redeem\n"
                    "`/coupon preferences` — Chọn code mới, sắp hết hạn hoặc digest tuần\n"
                    "`/coupon used <game> <code>` — Ẩn code bạn đã nhập\n"
                    "Chủ bot dùng `/coupon add`, `/coupon remove` và `/coupon status`. Hệ "
                    "thống hỗ trợ Brown Dust 2, NIKKE và Blue Archive. UID/nickname là tùy "
                    "chọn riêng tư; Peto không lưu mật khẩu/token game. Auto-redeem hiện "
                    "chỉ có BD2 và dùng endpoint bên thứ ba BD2 Pulse."
                ),
            )

        else:
            embed = build_embed(
                "📥 Universal Media Downloader",
                (
                    "Dùng `/download link:<URL> [format]` để tạo embed có tiêu đề, thumbnail, tác giả, "
                    "thời lượng, định dạng và nút tải. Bot không tự quét các link được gửi "
                    "trong chat. Toàn bộ panel và file tải đều **Only Visible to you**; "
                    "file chỉ được xử lý khi bạn bấm nút.\n\n"
                    "**Quy tắc định dạng**\n"
                    "🎵 **YouTube** — Chọn MP3 chất lượng cao hoặc MP4 360p/720p/1080p nếu video có sẵn; "
                    "video nhiều bản lồng tiếng sẽ ưu tiên audio gốc/default\n"
                    "🎬 **TikTok video** — Tải MP4 không watermark\n"
                    "🖼️ **TikTok photo** — Tải toàn bộ ảnh gốc theo từng file\n"
                    "🐦 **X / Twitter** — Tải video MP4, tối đa 4 ảnh gốc; ảnh động có cả "
                    "GIF thật và MP4 nhẹ hơn\n\n"
                    "TikTok dùng `yt-dlp` làm nguồn chính và tự chuyển sang TikWM "
                    "khi gặp challenge/rehydration. Link chỉ được gửi tới dịch vụ "
                    "fallback khi nguồn chính thất bại. File nằm trong giới hạn Discord "
                    "được gửi trực tiếp; file lớn dùng link riêng tư từ "
                    "`download.pearto.shop`, hết hạn sau 2 giờ. MP4 giữ nguyên luồng hình và ghép "
                    "audio phù hợp, không encode lại. Trong lúc chờ, Download Tracker hiển thị "
                    "giai đoạn, phần trăm, dung lượng, tốc độ và ETA khi nguồn có đủ dữ liệu.\n\n"
                    "Chỉ xử lý link video cụ thể dài dưới 60 phút. Giới hạn 10 phút của nút "
                    "tải trên Music Panel là luồng riêng. Một bài TikTok photo "
                    "được tải tối đa 35 ảnh; bot tự chia thành nhiều lượt gửi, mỗi lượt tối đa "
                    "10 file và vẫn tuân theo giới hạn upload của Discord. Playlist, "
                    "livestream, nội dung riêng tư và file vượt giới hạn upload "
                    "hiện tại của Discord sẽ bị từ chối. Mỗi người chỉ có một lượt "
                    "tải đang chạy; nút tải hết hạn sau 10 phút và file tạm được xóa "
                    "ngay sau khi gửi.\n\n"
                    "**Social Embed tự động**\n"
                    "Pixiv có card riêng gồm tác giả, mô tả, ảnh, thống kê và ngày đăng; "
                    "Ugoira được ghép thành MP4 để phát ngay trong Discord. "
                    "X/Twitter dùng FxTwitter; Instagram dùng vxInstagram. Sau khi preview "
                    "mới được gửi thành công, bot sẽ ẩn preview mặc định nếu có quyền "
                    "**Manage Messages**. Thêm `fxignore` hoặc `peto-noembed` vào tin để "
                    "bỏ qua. Đây chỉ là preview, không tự tải media.\n\n"
                    "-# Facebook và tải file Instagram hiện không nằm trong phạm vi hỗ trợ."
                ),
            )

        await self._show_embed(interaction, embed)


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
                "anime art và trợ lý AI Peto sẵn sàng trò chuyện cùng bạn.\n\n"
                "**Danh mục trợ giúp**\n"
                "📢 **Giới thiệu** — Tổng quan tính năng và công nghệ\n"
                "🎵 **Lệnh âm nhạc** — Phát nhạc, hàng đợi và voice\n"
                "🎨 **Art & AI** — Danbooru, Peto và bộ nhớ hội thoại\n"
                "🎮 **Game** — Limbus, Daily Reset và Gift Code\n"
                "📥 **Tải media** — TikTok video/ảnh, YouTube MP3/MP4 và X video/ảnh/GIF\n\n"
                "Chọn một danh mục trong menu bên dưới để xem chi tiết."
            ),
        )
        await interaction.response.send_message(embed=embed, view=HelpDropdown())


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
