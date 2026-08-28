"""Per-server notification settings controlled through Discord components."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from features.coupon_codes import GAMES as COUPON_GAMES
from features.daily_reset import BASE_GAMES
from guild_settings import GuildNotification, GuildSettingsStore


logger = logging.getLogger(__name__)

FEATURE_LABELS = {
    "daily_reset": "Daily Reset",
    "projectmoon": "Project Moon YouTube",
    "coupon": "Coupon Code",
}
TARGET_LABELS = {
    "projectmoon": {"official_youtube": "ProjectMoon Official"},
    "daily_reset": {game.slug: game.name for game in BASE_GAMES},
    "coupon": {slug: game.name for slug, game in COUPON_GAMES.items()},
}


def can_manage_guild(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and (permissions.manage_guild or permissions.administrator))


class SettingsChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, panel: "NotificationSettingsView"):
        self.panel = panel
        super().__init__(
            placeholder="Chọn kênh nhận thông báo",
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.news,
            ],
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        await self.panel.store.set_channel(
            self.panel.guild_id,
            self.panel.feature,
            self.panel.target,
            int(channel.id),
            interaction.user.id,
        )
        await self.panel.refresh(interaction)


class SettingsRoleSelect(discord.ui.RoleSelect):
    def __init__(self, panel: "NotificationSettingsView"):
        self.panel = panel
        super().__init__(
            placeholder="Chọn role được ping",
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        if int(role.id) == self.panel.guild_id:
            return await interaction.response.send_message(
                "❌ Không thể dùng `@everyone` làm role thông báo.", ephemeral=True
            )
        await self.panel.store.set_role(
            self.panel.guild_id,
            self.panel.feature,
            self.panel.target,
            int(role.id),
            interaction.user.id,
        )
        await self.panel.refresh(interaction)


class NotificationSettingsView(discord.ui.View):
    def __init__(
        self,
        cog: "Settings",
        guild_id: int,
        user_id: int,
        feature: str,
        target: str,
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.store = cog.store
        self.guild_id = int(guild_id)
        self.user_id = int(user_id)
        self.feature = feature
        self.target = target
        self.add_item(SettingsChannelSelect(self))
        self.add_item(SettingsRoleSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Hãy mở bảng `/settings notifications` của riêng bạn.",
                ephemeral=True,
            )
            return False
        if interaction.guild_id != self.guild_id or not (
            can_manage_guild(interaction) or await self.cog.bot.is_owner(interaction.user)
        ):
            await interaction.response.send_message(
                "❌ Bạn cần quyền **Manage Server** để thay đổi cấu hình này.",
                ephemeral=True,
            )
            return False
        return True

    async def build_embed(self) -> discord.Embed:
        setting = await self.store.get(self.guild_id, self.feature, self.target)
        enabled = bool(setting and setting.enabled)
        self.toggle.label = "Tắt thông báo" if enabled else "Bật thông báo"
        self.toggle.style = (
            discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success
        )
        channel = f"<#{setting.channel_id}>" if setting and setting.channel_id else "Chưa chọn"
        role = f"<@&{setting.role_id}>" if setting and setting.role_id else "Không ping"
        embed = discord.Embed(
            title="⚙️ Cấu hình thông báo Peto",
            description=(
                f"**Tính năng:** {FEATURE_LABELS[self.feature]}\n"
                f"**Nội dung:** {TARGET_LABELS[self.feature][self.target]}\n\n"
                f"**Trạng thái:** {'🟢 Đang bật' if enabled else '⚫ Đang tắt'}\n"
                f"**Kênh:** {channel}\n"
                f"**Role ping:** {role}"
            ),
            color=0x57F287 if enabled else 0x5865F2,
        )
        embed.set_footer(
            text="Chỉ người có quyền Manage Server hoặc chủ bot mới thay đổi được."
        )
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        setting = await self.store.get(self.guild_id, self.feature, self.target)
        self.toggle.label = "Tắt thông báo" if setting and setting.enabled else "Bật thông báo"
        self.toggle.style = (
            discord.ButtonStyle.danger
            if setting and setting.enabled
            else discord.ButtonStyle.success
        )
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Bật thông báo", emoji="🔔", style=discord.ButtonStyle.success, row=2)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        current = await self.store.get(self.guild_id, self.feature, self.target)
        try:
            await self.store.set_enabled(
                self.guild_id,
                self.feature,
                self.target,
                not bool(current and current.enabled),
                interaction.user.id,
            )
        except ValueError as error:
            return await interaction.response.send_message(f"❌ {error}", ephemeral=True)
        await self.refresh(interaction)

    @discord.ui.button(label="Gửi thử", emoji="🧪", style=discord.ButtonStyle.primary, row=2)
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        setting = await self.store.get(self.guild_id, self.feature, self.target)
        if setting is None or setting.channel_id is None:
            return await interaction.response.send_message(
                "❌ Hãy chọn kênh trước khi gửi thử.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            jump_url = await self.cog.send_feature_test(setting)
        except Exception as error:
            logger.exception(
                "Gửi thử cấu hình %s/%s guild=%s thất bại",
                self.feature,
                self.target,
                self.guild_id,
            )
            return await interaction.followup.send(
                f"❌ Gửi thử thất bại: `{str(error)[:300]}`", ephemeral=True
            )
        await interaction.followup.send(
            f"✅ Đã gửi bản thử: [mở tin nhắn]({jump_url}). Không ping role.",
            ephemeral=True,
        )

    @discord.ui.button(label="Bỏ role", emoji="🔕", style=discord.ButtonStyle.secondary, row=2)
    async def clear_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self.store.set_role(
            self.guild_id,
            self.feature,
            self.target,
            None,
            interaction.user.id,
        )
        await self.refresh(interaction)

    @discord.ui.button(label="Xóa cấu hình", emoji="🗑️", style=discord.ButtonStyle.danger, row=3)
    async def clear(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self.store.clear(
            self.guild_id,
            self.feature,
            self.target,
            interaction.user.id,
        )
        await self.refresh(interaction)


class Settings(commands.Cog):
    settings = app_commands.Group(
        name="settings",
        description="Cấu hình Peto riêng cho server này",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = GuildSettingsStore()

    async def cog_load(self) -> None:
        await self.store.init()

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id and (
            can_manage_guild(interaction) or await self.bot.is_owner(interaction.user)
        ):
            return True
        await interaction.response.send_message(
            "❌ Bạn cần quyền **Manage Server** để cấu hình Peto.", ephemeral=True
        )
        return False

    async def target_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        feature_option = getattr(interaction.namespace, "feature", "")
        feature = (
            feature_option.value
            if isinstance(feature_option, app_commands.Choice)
            else str(feature_option)
        )
        labels = TARGET_LABELS.get(feature, {})
        needle = current.casefold().strip()
        return [
            app_commands.Choice(name=label[:100], value=target)
            for target, label in labels.items()
            if not needle or needle in label.casefold() or needle in target.casefold()
        ][:25]

    @settings.command(
        name="notifications",
        description="Chọn kênh và role cho thông báo của server",
    )
    @app_commands.describe(
        feature="Loại thông báo",
        target="Game/nội dung cần cấu hình; Project Moon có thể để trống",
    )
    @app_commands.choices(
        feature=[
            app_commands.Choice(name=label, value=value)
            for value, label in FEATURE_LABELS.items()
        ]
    )
    @app_commands.autocomplete(target=target_autocomplete)
    async def notifications(
        self,
        interaction: discord.Interaction,
        feature: app_commands.Choice[str],
        target: str = "",
    ) -> None:
        if not await self._allowed(interaction):
            return
        feature_value = feature.value
        normalized_target = target.strip().casefold().replace("-", "_")
        if feature_value == "projectmoon" and not normalized_target:
            normalized_target = "official_youtube"
        if normalized_target not in TARGET_LABELS.get(feature_value, {}):
            return await interaction.response.send_message(
                "❌ Nội dung không hợp lệ. Hãy chọn một gợi ý trong danh sách.",
                ephemeral=True,
            )
        view = NotificationSettingsView(
            self,
            interaction.guild_id,
            interaction.user.id,
            feature_value,
            normalized_target,
        )
        await interaction.response.send_message(
            embed=await view.build_embed(), view=view, ephemeral=True
        )

    async def send_feature_test(self, setting: GuildNotification) -> str:
        cog_names = {
            "daily_reset": "DailyReset",
            "projectmoon": "ProjectMoonYouTube",
            "coupon": "CouponCodes",
        }
        cog = self.bot.get_cog(cog_names[setting.feature])
        if cog is None or not hasattr(cog, "send_settings_preview"):
            raise RuntimeError("Tính năng này chưa sẵn sàng; hãy thử lại sau.")
        return await cog.send_settings_preview(setting)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Settings(bot))
