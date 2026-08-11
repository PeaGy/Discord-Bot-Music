"""Discord UI cho Study Mode của Peto."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import discord


logger = logging.getLogger(__name__)


DISCORD_MESSAGE_LIMIT = 2000
STUDY_VIEW_TIMEOUT = 15 * 60
MAX_EXPORT_LINES = 160


def truncate_for_discord(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n\n*(...cắt bớt; dùng **Xuất PNG** để xem bản dài hơn)*"
    return text[: limit - len(suffix)].rstrip() + suffix


@dataclass
class StudySession:
    owner_id: int
    display_name: str
    problem_text: str
    attachments: list[Any] = field(default_factory=list)
    latest_solution: str = ""
    extracted_problem: str = ""


def _font_candidates() -> list[str]:
    configured = os.getenv("STUDY_FONT_PATH")
    candidates = [
        configured,
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    return [path for path in candidates if path]


def _load_font(size: int):
    from PIL import ImageFont

    for path in _font_candidates():
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _plain_export_text(text: str) -> str:
    text = re.sub(r"```(?:text)?", "", text, flags=re.IGNORECASE)
    text = text.replace("```", "")
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text.strip()


def _wrap_visual_line(draw, text: str, font, max_width: int) -> list[str]:
    if not text:
        return [""]

    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = ""

        # Chuỗi công thức/URL dài không có khoảng trắng: cắt theo ký tự.
        chunk = ""
        for char in word:
            candidate = f"{chunk}{char}"
            if chunk and draw.textlength(candidate, font=font) > max_width:
                lines.append(chunk)
                chunk = char
            else:
                chunk = candidate
        current = chunk

    if current:
        lines.append(current)
    return lines or [""]


def render_solution_png(problem_text: str, solution: str) -> io.BytesIO:
    """Render lời giải thành PNG tối, dễ đọc và không cần ghi file tạm."""
    from PIL import Image, ImageDraw

    width = 1400
    margin = 64
    content_width = width - margin * 2
    title_font = _load_font(38)
    body_font = _load_font(27)
    small_font = _load_font(22)

    measuring_image = Image.new("RGB", (width, 100), (43, 45, 49))
    measuring_draw = ImageDraw.Draw(measuring_image)

    problem = _plain_export_text(problem_text) or "Bài tập từ ảnh đính kèm"
    body = _plain_export_text(solution) or "Chưa có lời giải để xuất."
    logical_lines = [
        ("heading", "ĐỀ BÀI"),
        *(("body", line) for line in problem.splitlines()),
        ("body", ""),
        ("heading", "LỜI GIẢI"),
        *(("body", line) for line in body.splitlines()),
    ]

    visual_lines: list[tuple[str, str]] = []
    for kind, line in logical_lines:
        font = title_font if kind == "heading" else body_font
        for wrapped in _wrap_visual_line(
            measuring_draw,
            line,
            font,
            content_width,
        ):
            visual_lines.append((kind, wrapped))

    truncated = len(visual_lines) > MAX_EXPORT_LINES
    visual_lines = visual_lines[:MAX_EXPORT_LINES]
    if truncated:
        visual_lines.append(("body", "... Nội dung quá dài nên ảnh đã được rút gọn."))

    title_height = 58
    body_height = 40
    footer_height = 70
    height = margin * 2 + footer_height + sum(
        title_height if kind == "heading" else body_height
        for kind, _ in visual_lines
    )
    image = Image.new("RGB", (width, max(height, 500)), (43, 45, 49))
    draw = ImageDraw.Draw(image)

    y = margin
    for kind, line in visual_lines:
        if kind == "heading":
            draw.text((margin, y), line, font=title_font, fill=(88, 101, 242))
            y += title_height
        else:
            draw.text((margin, y), line, font=body_font, fill=(238, 238, 240))
            y += body_height

    draw.line((margin, y + 16, width - margin, y + 16), fill=(78, 80, 88), width=2)
    draw.text(
        (margin, y + 30),
        "Tracen Jukebox • Peto Study Mode",
        font=small_font,
        fill=(170, 170, 176),
    )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


class AnswerCheckModal(discord.ui.Modal, title="Kiểm tra đáp án"):
    answer = discord.ui.TextInput(
        label="Đáp án hoặc cách làm của bạn",
        placeholder="Nhập đáp án và các bước bạn đã làm...",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )

    def __init__(self, view: "StudyView"):
        super().__init__()
        self.study_view = view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.study_view.cog.generate_study_response(
            self.study_view.session,
            action="check",
            student_answer=self.answer.value,
        )
        await interaction.followup.send(
            truncate_for_discord(result),
            ephemeral=True,
        )


class StudyView(discord.ui.View):
    def __init__(self, cog, session: StudySession):
        super().__init__(timeout=STUDY_VIEW_TIMEOUT)
        self.cog = cog
        self.session = session
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.session.owner_id:
            return True
        await interaction.response.send_message(
            "❌ Đây là phiên Study Mode của người gửi đề.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Gợi ý", emoji="💡", style=discord.ButtonStyle.secondary)
    async def hint(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.generate_study_response(
            self.session,
            action="hint",
        )
        await interaction.followup.send(
            truncate_for_discord(result),
            ephemeral=True,
        )

    @discord.ui.button(label="Giải chi tiết", emoji="🧠", style=discord.ButtonStyle.primary)
    async def detailed(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.generate_study_response(
            self.session,
            action="detailed",
        )
        self.session.latest_solution = result
        await interaction.followup.send(
            truncate_for_discord(result),
            ephemeral=True,
        )

    @discord.ui.button(label="Kiểm tra đáp án", emoji="✅", style=discord.ButtonStyle.success)
    async def check_answer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AnswerCheckModal(self))

    @discord.ui.button(label="Chép đề", emoji="🔎", style=discord.ButtonStyle.secondary)
    async def extract_problem(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.generate_study_response(
            self.session,
            action="extract",
        )
        if not result.startswith("❌"):
            self.session.extracted_problem = result
        await interaction.followup.send(
            truncate_for_discord(result),
            ephemeral=True,
        )

    @discord.ui.button(label="Xuất PNG", emoji="🖼️", style=discord.ButtonStyle.secondary)
    async def export_png(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            image_data = await asyncio.to_thread(
                render_solution_png,
                self.session.extracted_problem or self.session.problem_text,
                self.session.latest_solution,
            )
            upload_limit = getattr(interaction, "filesize_limit", None)
            if upload_limit and image_data.getbuffer().nbytes > upload_limit:
                return await interaction.followup.send(
                    "❌ Ảnh lời giải vượt giới hạn upload hiện tại của Discord.",
                    ephemeral=True,
                )

            upload = discord.File(image_data, filename="peto-study-solution.png")
            try:
                await interaction.followup.send(
                    "🖼️ Lời giải của bạn đây:",
                    file=upload,
                    ephemeral=True,
                )
            finally:
                upload.close()
        except Exception:
            logger.exception("Không thể render ảnh Study Mode")
            await interaction.followup.send(
                "❌ Không thể xuất ảnh lời giải lúc này.",
                ephemeral=True,
            )
