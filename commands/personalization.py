from dataclasses import replace

import discord
from discord import app_commands
from discord.ext import commands

import user_memory
from guild_ai_settings import GuildAISettingsStore
from user_memory import PERSONALIZATION_MAX_LENGTH, Personalization


_TITLE = "Cá nhân hóa Peto"
_NOT_OWNER = "❌ Chỉ chủ bảng cá nhân hóa mới dùng được nút này."
_STALE = "Bản hướng dẫn đã thay đổi. Hãy thao tác lại trên bảng mới nhất nhé."


class PersonalizationEditor(discord.ui.Modal, title=_TITLE):
    def __init__(
        self, cog: "PersonalizationCommand", user_id: int, current: Personalization | None
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.current = current
        self.instructions = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=current.instructions if current else None,
            placeholder="Xưng mình–bạn, trả lời ngắn gọn.",
            max_length=PERSONALIZATION_MAX_LENGTH,
        )
        self.add_item(discord.ui.Label(
            text="Hướng dẫn cho Peto",
            description="Bạn muốn Peto xưng hô và trả lời thế nào?",
            component=self.instructions,
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(_NOT_OWNER, ephemeral=True)
        return False

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = self.instructions.value.strip()
        if not 0 < len(text) <= PERSONALIZATION_MAX_LENGTH:
            await interaction.response.send_message(
                "Hãy nhập hướng dẫn từ 1 đến 1.500 ký tự nhé.", ephemeral=True
            )
            return
        replacement = Personalization(text, enabled=self.current is None or self.current.enabled)
        await self.cog.update(interaction, self.current, replacement)


class PersonalizationPanel(discord.ui.View):
    def __init__(
        self,
        cog: "PersonalizationCommand",
        user_id: int,
        current: Personalization | None,
        *,
        confirming: bool = False,
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.current = current
        self.confirming = confirming
        if confirming:
            self.remove_item(self.edit)
            self.remove_item(self.toggle)
            self.delete.label = "Xác nhận xóa"
        else:
            self.remove_item(self.cancel_delete)
            if current is None:
                self.remove_item(self.toggle)
                self.remove_item(self.delete)
                self.edit.label = "Thêm hướng dẫn"
            else:
                self.toggle.label = "Tắt" if current.enabled else "Bật"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(_NOT_OWNER, ephemeral=True)
        return False

    @discord.ui.button(label="Chỉnh sửa", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_editor(interaction)

    @discord.ui.button(label="Tắt", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current is not None:
            replacement = replace(self.current, enabled=not self.current.enabled)
            await self.cog.update(interaction, self.current, replacement)

    @discord.ui.button(label="Xóa", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.confirming:
            await self.cog.update(interaction, self.current, None)
        else:
            await interaction.response.defer()
            await self.cog.refresh_panel(interaction, confirm_delete=self.current)

    @discord.ui.button(label="Hủy", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        await self.cog.refresh_panel(interaction)


class PersonalizationCommand(commands.Cog):
    def __init__(self):
        self.ai_settings = GuildAISettingsStore()

    async def cog_load(self) -> None:
        await user_memory.init_db()
        await self.ai_settings.init()

    async def restriction(self, interaction: discord.Interaction, scope: str) -> str | None:
        if await user_memory.is_anonymous_mode(interaction.user.id, scope):
            return (
                "🕶️ Cá nhân hóa đang tạm ngưng vì bạn đã bật Ẩn danh ở đây. "
                "Dùng `/andanh` để tắt Ẩn danh trước khi mở lại hướng dẫn."
            )
        if interaction.guild_id is not None:
            policy = await self.ai_settings.get(interaction.guild_id)
            if policy is not None and not policy.memory_enabled:
                return (
                    "Cá nhân hóa đang tạm ngưng vì server đã tắt trí nhớ cá nhân. "
                    "Bạn có thể chỉnh hướng dẫn trong DM với Peto."
                )
        return None

    async def open_editor(self, interaction: discord.Interaction) -> None:
        scope = user_memory.scope_for_guild(interaction.guild_id)
        reason = await self.restriction(interaction, scope)
        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return
        user_id = interaction.user.id
        current = await user_memory.get_personalization(user_id, scope)
        await interaction.response.send_modal(PersonalizationEditor(self, user_id, current))

    async def update(
        self,
        interaction: discord.Interaction,
        previous: Personalization | None,
        replacement: Personalization | None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=interaction.message is None)
        scope = user_memory.scope_for_guild(interaction.guild_id)
        reason = await self.restriction(interaction, scope)
        if reason:
            await interaction.edit_original_response(content=reason, embed=None, view=None)
            return
        saved = await user_memory.update_personalization(
            interaction.user.id, scope, previous=previous, replacement=replacement
        )
        notice = None if saved else _STALE
        await self.refresh_panel(interaction, notice=notice)

    async def refresh_panel(
        self,
        interaction: discord.Interaction,
        *,
        notice: str | None = None,
        confirm_delete: Personalization | None = None,
    ) -> None:
        scope = user_memory.scope_for_guild(interaction.guild_id)
        reason = await self.restriction(interaction, scope)
        if reason:
            await interaction.edit_original_response(content=reason, embed=None, view=None)
            return

        user_id = interaction.user.id
        current = await user_memory.get_personalization(user_id, scope)
        confirming = confirm_delete is not None and confirm_delete == current
        if confirming:
            description = "Xóa hướng dẫn cá nhân hóa của bạn trên mọi server và DM?"
        elif current is None:
            description = "Bạn chưa lưu hướng dẫn cá nhân hóa."
        else:
            status = "🟢 Đang bật" if current.enabled else "⚫ Đang tắt"
            text = discord.utils.escape_markdown(discord.utils.escape_mentions(current.instructions))
            description = f"{status}\n\n**Hướng dẫn đã lưu**\n{text}"

        await interaction.edit_original_response(
            content=_STALE if confirm_delete is not None and not confirming else notice,
            embed=discord.Embed(title=_TITLE, description=description, color=0x5865F2),
            view=PersonalizationPanel(self, user_id, current, confirming=confirming),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="personalization",
        description="Đặt hướng dẫn riêng về cách Peto xưng hô và trả lời bạn",
    )
    async def personalization(self, interaction: discord.Interaction) -> None:
        await self.open_editor(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PersonalizationCommand())
