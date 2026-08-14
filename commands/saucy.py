"""Reverse-image-search panel using public search URLs, without API keys."""

from __future__ import annotations

import re
from urllib import parse

import discord
from discord import app_commands
from discord.ext import commands


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
EMBED_COLOR = 0x4285F4


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").casefold()
    filename = (attachment.filename or "").casefold()
    return content_type.startswith("image/") or filename.endswith(IMAGE_EXTENSIONS)


def _looks_like_image_url(url: str) -> bool:
    try:
        path = parse.urlsplit(url).path.casefold()
    except ValueError:
        return False
    return path.endswith(IMAGE_EXTENSIONS)


def image_url_from_message(message: discord.Message) -> str | None:
    """Prefer the original attachment, then an embed image, then a direct URL."""
    for attachment in message.attachments:
        if _is_image_attachment(attachment):
            return attachment.url

    for embed in message.embeds:
        if embed.image and embed.image.url:
            return str(embed.image.url)
        if embed.thumbnail and embed.thumbnail.url:
            return str(embed.thumbnail.url)

    for match in URL_RE.finditer(message.content or ""):
        candidate = match.group(0).rstrip(".,!?;:)]}>'\"")
        if _looks_like_image_url(candidate):
            return candidate
    return None


def reverse_search_urls(image_url: str) -> dict[str, str]:
    encoded = parse.quote_plus(image_url)
    return {
        "Google Lens": f"https://lens.google.com/uploadbyurl?url={encoded}",
        "SauceNAO": f"https://saucenao.com/search.php?url={encoded}",
        "IQDB": f"https://iqdb.org/?url={encoded}",
        "TinEye": f"https://www.tineye.com/search?url={encoded}",
        "Yandex": f"https://yandex.com/images/search?url={encoded}&rpt=imageview",
        "Bing": f"https://www.bing.com/images/searchbyimage?cbir=sbi&imgurl={encoded}",
    }


class SauceSearchView(discord.ui.View):
    def __init__(self, image_url: str):
        super().__init__(timeout=None)
        buttons = (
            ("Google Lens", "🔎", 0),
            ("SauceNAO", "🧩", 0),
            ("IQDB", "🎨", 0),
            ("TinEye", "👁️", 0),
            ("Yandex", "🔍", 0),
            ("Bing", "🖼️", 1),
        )
        urls = reverse_search_urls(image_url)
        for label, emoji, row in buttons:
            self.add_item(
                discord.ui.Button(
                    label=label,
                    emoji=emoji,
                    style=discord.ButtonStyle.link,
                    url=urls[label],
                    row=row,
                )
            )


def build_sauce_embed(image_url: str) -> discord.Embed:
    embed = discord.Embed(
        title="🔎 Tìm nguồn ảnh",
        description=(
            "Chọn một công cụ bên dưới để mở kết quả tìm kiếm ngược. "
            "Peto chỉ tạo liên kết; kết quả được hiển thị trên trang của từng dịch vụ.\n\n"
            "-# Khi bạn nhấn nút, dịch vụ tương ứng sẽ nhận URL của ảnh để tìm kiếm."
        ),
        color=EMBED_COLOR,
    )
    embed.set_image(url=image_url)
    embed.set_footer(text="Nên mở kết quả sớm vì URL ảnh Discord có thể hết hạn.")
    return embed


async def send_sauce_panel(
    interaction: discord.Interaction,
    image_url: str,
) -> None:
    if not image_url.startswith(("https://", "http://")):
        await interaction.response.send_message(
            "❌ Ảnh này không có URL công khai để tìm kiếm.",
            ephemeral=True,
        )
        return
    try:
        view = SauceSearchView(image_url)
    except ValueError:
        await interaction.response.send_message(
            "❌ URL ảnh quá dài để tạo các nút tìm kiếm.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        embed=build_sauce_embed(image_url),
        view=view,
        ephemeral=True,
    )


class Saucy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sauce_menu = app_commands.ContextMenu(
            name="Tìm nguồn ảnh",
            callback=self.find_from_message,
        )
        self.bot.tree.add_command(self.sauce_menu)

    def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.sauce_menu.name, type=self.sauce_menu.type)

    @app_commands.command(
        name="saucy",
        description="Tạo các nút tìm nguồn ảnh bằng Google Lens, SauceNAO và dịch vụ khác",
    )
    @app_commands.describe(image="Dán hoặc tải ảnh cần tìm nguồn")
    async def saucy(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
    ) -> None:
        if not _is_image_attachment(image):
            await interaction.response.send_message(
                "❌ Tệp đính kèm không phải ảnh được hỗ trợ.",
                ephemeral=True,
            )
            return
        await send_sauce_panel(interaction, image.url)

    async def find_from_message(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        image_url = image_url_from_message(message)
        if image_url is None:
            await interaction.response.send_message(
                "❌ Tin nhắn này không có ảnh phù hợp để tìm nguồn.",
                ephemeral=True,
            )
            return
        await send_sauce_panel(interaction, image_url)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Saucy(bot))
