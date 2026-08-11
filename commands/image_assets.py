import logging

import discord
from discord import app_commands
from discord.ext import commands

from features.ai_actions import create_asset_files


logger = logging.getLogger(__name__)


class ImageAssets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _create(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        *,
        sticker: bool,
        emoji: bool,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            files = await create_asset_files(image, sticker=sticker, emoji=emoji)
        except ValueError as error:
            return await interaction.followup.send(f"❌ {error}", ephemeral=True)
        except Exception:
            logger.exception("Không xử lý được attachment=%s", image.id)
            return await interaction.followup.send("❌ Không xử lý được ảnh này.", ephemeral=True)

        kind = "sticker" if sticker else "emoji"
        await interaction.followup.send(
            f"✨ **{kind.capitalize()} đã sẵn sàng.** Bot đã crop vuông, căn giữa "
            "và thử xóa nền đồng nhất ở mép ảnh.",
            files=files,
            ephemeral=True,
        )

    @app_commands.command(name="sticker", description="Tạo PNG sticker 320×320 từ ảnh")
    @app_commands.describe(image="Dán hoặc tải ảnh cần xử lý")
    async def sticker(self, interaction: discord.Interaction, image: discord.Attachment):
        await self._create(interaction, image, sticker=True, emoji=False)

    @app_commands.command(name="emoji", description="Tạo PNG emoji 128×128 từ ảnh")
    @app_commands.describe(image="Dán hoặc tải ảnh cần xử lý")
    async def emoji(self, interaction: discord.Interaction, image: discord.Attachment):
        await self._create(interaction, image, sticker=False, emoji=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ImageAssets(bot))
