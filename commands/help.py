import discord
from discord import app_commands
from discord.ext import commands
from peto_help_catalog import HELP_CATEGORIES


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
    footer = "Pearto • Chọn danh mục bên dưới để xem thêm"
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
        self.current_title = str(embed.title or "Pearto Help")
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
                description="Tổng quan về Pearto",
                emoji="📢",
                value="info",
            ),
            discord.SelectOption(
                label="Lệnh âm nhạc",
                description="Các lệnh phát nhạc",
                emoji="🎵",
                value="commands",
            ),
            discord.SelectOption(
                label="Art & AI",
                description="Danbooru, AI và bộ nhớ Peto",
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

        title, description = HELP_CATEGORIES.get(value, HELP_CATEGORIES["media"])
        embed = build_embed(title, description)

        await self._show_embed(interaction, embed)


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="help",
        description="Mở bảng hướng dẫn sử dụng Pearto",
    )
    async def help(self, interaction: discord.Interaction):
        embed = build_embed(
            "🍐 Pearto Help Panel",
            (
                "Pearto kết hợp phát nhạc chất lượng cao, radio internet, "
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
