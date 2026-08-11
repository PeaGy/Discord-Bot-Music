"""Context menu AI và công cụ tạo sticker/emoji ngay trong Discord."""

import asyncio
import io
import logging
from collections import deque

import discord
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger(__name__)
MAX_ASSET_IMAGE_BYTES = 20 * 1024 * 1024


class AskPetoModal(discord.ui.Modal, title="Hỏi Peto về tin nhắn này"):
    request = discord.ui.TextInput(
        label="Bạn muốn Peto làm gì?",
        placeholder="Ví dụ: giải thích, dịch, tóm tắt hoặc viết câu trả lời…",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, bot: commands.Bot, target: discord.Message):
        super().__init__()
        self.bot = bot
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        cog = self.bot.get_cog("GrokChat")
        if cog is None:
            return await interaction.followup.send("❌ Peto AI chưa sẵn sàng.", ephemeral=True)
        answer = await cog.answer_context_message(
            interaction,
            self.target,
            str(self.request.value),
        )
        verification_question = (
            f"{self.request.value}\nTin nhắn được chọn: {self.target.clean_content[:1500]}"
        )
        view = SourceCheckView(
            cog, interaction.user.id, verification_question, answer
        ) if cog._looks_like_factual_request(str(self.request.value)) else None
        send_kwargs = {
            "content": answer[:2000] or "Peto chưa tạo được câu trả lời.",
            "ephemeral": True,
        }
        # discord.py 2.7 không chấp nhận truyền view=None vào webhook followup.
        if view is not None:
            send_kwargs["view"] = view
        await interaction.followup.send(**send_kwargs)


class SourceCheckView(discord.ui.View):
    def __init__(self, cog, owner_id: int, question: str, answer: str):
        super().__init__(timeout=10 * 60)
        self.cog = cog
        self.owner_id = owner_id
        self.question = question
        self.answer = answer

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("❌ Hãy hỏi Peto bằng tin nhắn của bạn nhé.", ephemeral=True)
        return False

    @discord.ui.button(label="Kiểm tra nguồn", emoji="🔎", style=discord.ButtonStyle.secondary)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.verify_answer(self.question, self.answer)
        await interaction.followup.send(result[:2000], ephemeral=True)


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").casefold()
    filename = (attachment.filename or "").casefold()
    return content_type.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp"))


def _image_attachment(message: discord.Message) -> discord.Attachment | None:
    for attachment in message.attachments:
        if _is_image_attachment(attachment):
            return attachment
    return None


def _remove_connected_background(image):
    """Xóa nền màu tương đối đồng nhất nối với mép ảnh; giữ nền phức tạp."""
    from PIL import Image

    image.thumbnail((768, 768), Image.Resampling.LANCZOS)
    image = image.convert("RGBA")
    if image.getchannel("A").getextrema()[0] < 250:
        return image
    width, height = image.size
    pixels = image.load()
    corners = [pixels[0, 0][:3], pixels[width - 1, 0][:3], pixels[0, height - 1][:3], pixels[width - 1, height - 1][:3]]
    reference = tuple(sum(color[i] for color in corners) // 4 for i in range(3))
    spread = max(sum((color[i] - reference[i]) ** 2 for i in range(3)) ** 0.5 for color in corners)
    if spread > 75:
        return image

    def similar(x, y):
        color = pixels[x, y]
        return sum((color[i] - reference[i]) ** 2 for i in range(3)) <= 48 ** 2

    pending = deque()
    seen = set()
    for x in range(width):
        pending.extend(((x, 0), (x, height - 1)))
    for y in range(height):
        pending.extend(((0, y), (width - 1, y)))
    while pending:
        x, y = pending.popleft()
        if (x, y) in seen or not similar(x, y):
            continue
        seen.add((x, y))
        r, g, b, _ = pixels[x, y]
        pixels[x, y] = (r, g, b, 0)
        if x: pending.append((x - 1, y))
        if x + 1 < width: pending.append((x + 1, y))
        if y: pending.append((x, y - 1))
        if y + 1 < height: pending.append((x, y + 1))
    return image


def _render_asset(source, size: int) -> io.BytesIO:
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(Image.open(io.BytesIO(source)))
    image = _remove_connected_background(image)
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    margin = max(4, size // 20)
    image.thumbnail((size - margin * 2, size - margin * 2), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


async def create_asset_files(
    attachment: discord.Attachment,
    *,
    sticker: bool = False,
    emoji: bool = False,
) -> list[discord.File]:
    """Đọc attachment một lần rồi tạo các PNG được yêu cầu, hoàn toàn cục bộ."""
    if not (sticker or emoji):
        raise ValueError("Cần chọn ít nhất một loại ảnh đầu ra.")
    if attachment.size and attachment.size > MAX_ASSET_IMAGE_BYTES:
        raise ValueError("Ảnh lớn hơn 20 MiB.")
    if not _is_image_attachment(attachment):
        raise ValueError("Tệp đính kèm không phải ảnh được hỗ trợ.")
    raw = await attachment.read()
    jobs = []
    labels = []
    if sticker:
        jobs.append(asyncio.to_thread(_render_asset, raw, 320))
        labels.append("peto-sticker-320.png")
    if emoji:
        jobs.append(asyncio.to_thread(_render_asset, raw, 128))
        labels.append("peto-emoji-128.png")
    outputs = await asyncio.gather(*jobs)
    return [discord.File(output, filename=name) for output, name in zip(outputs, labels)]


class AIActions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ask_menu = app_commands.ContextMenu(name="Hỏi Peto", callback=self.ask_peto)
        self.sticker_menu = app_commands.ContextMenu(name="Tạo sticker & emoji", callback=self.make_sticker)
        self.bot.tree.add_command(self.ask_menu)
        self.bot.tree.add_command(self.sticker_menu)

    def cog_unload(self):
        self.bot.tree.remove_command(self.ask_menu.name, type=self.ask_menu.type)
        self.bot.tree.remove_command(self.sticker_menu.name, type=self.sticker_menu.type)

    async def ask_peto(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.send_modal(AskPetoModal(self.bot, message))

    async def make_sticker(self, interaction: discord.Interaction, message: discord.Message):
        attachment = _image_attachment(message)
        if attachment is None:
            return await interaction.response.send_message("❌ Tin nhắn này không có ảnh phù hợp.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            files = await create_asset_files(attachment, sticker=True, emoji=True)
            await interaction.followup.send(
                "✨ Đã căn giữa, crop vuông và thử xóa nền nối với mép ảnh. "
                "Hai file đã sẵn sàng để tải lên Discord.",
                files=files,
                ephemeral=True,
            )
        except Exception:
            logger.exception("Không thể tạo sticker/emoji từ message=%s", message.id)
            await interaction.followup.send("❌ Không xử lý được ảnh này.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AIActions(bot))
