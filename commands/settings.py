"""Per-server notification settings controlled through Discord components."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from features.coupon_codes import GAMES as COUPON_GAMES
from features.daily_reset import BASE_GAMES
from guild_ai_settings import (
    AI_CAPABILITIES,
    GLOBAL_MAX_CONCURRENT,
    GLOBAL_MAX_VIDEO_SECONDS,
    ROLE_CAPABILITIES,
    GuildAIPolicy,
    GuildAISettingsStore,
)
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

AI_CAPABILITY_LABELS = {
    "memory": "Trí nhớ cá nhân",
    "web": "Web và đọc liên kết",
    "limbus": "Kiến thức Limbus",
    "study": "Study Mode",
    "image_read": "Đọc ảnh",
    "image_generation": "Tạo và sửa ảnh AI",
    "video": "Đọc video ngắn",
    "danbooru": "Tìm ảnh Danbooru",
    "music": "Điều khiển nhạc bằng lời nói",
}
AI_ROLE_LABELS = {
    "chat": "Chat AI nói chung",
    "web": "Web",
    "limbus": "Limbus",
    "study": "Study Mode",
    "image": "Đọc/tạo ảnh",
    "video": "Video",
    "music": "Điều khiển nhạc",
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


class AIResponseModeSelect(discord.ui.Select):
    def __init__(self, panel: "AISettingsView", policy: GuildAIPolicy):
        self.panel = panel
        options = [
            discord.SelectOption(
                label="Chỉ mention hoặc reply",
                value="mention",
                description="An toàn cho mọi kênh; đây là cách Peto đang hoạt động.",
                default=policy.response_mode == "mention",
            ),
            discord.SelectOption(
                label="Trò chuyện tự nhiên trong kênh đã chọn",
                value="channels",
                description="Không cần mention trong danh sách kênh AI.",
                default=policy.response_mode == "channels",
            ),
            discord.SelectOption(
                label="Tắt AI trong server",
                value="off",
                description="DM và server khác không bị ảnh hưởng.",
                default=policy.response_mode == "off",
            ),
        ]
        super().__init__(placeholder="Chế độ phản hồi", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.panel.store.update(
            self.panel.guild_id,
            interaction.user.id,
            response_mode=self.values[0],
        )
        await self.panel.replace(interaction)


class AIChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, panel: "AISettingsView"):
        self.panel = panel
        super().__init__(
            placeholder="Chọn tối đa 10 kênh được dùng AI",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=10,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.panel.store.set_channels(
            self.panel.guild_id, [int(channel.id) for channel in self.values]
        )
        await self.panel.replace(interaction)


class AIRoleSelect(discord.ui.RoleSelect):
    def __init__(self, panel: "AISettingsView"):
        self.panel = panel
        super().__init__(
            placeholder=f"Role được dùng: {AI_ROLE_LABELS[panel.role_capability]}",
            min_values=1,
            max_values=10,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role_ids = [int(role.id) for role in self.values]
        if self.panel.guild_id in role_ids:
            return await interaction.response.send_message(
                "❌ Không cần chọn `@everyone`; hãy bấm **Bỏ giới hạn role**.",
                ephemeral=True,
            )
        await self.panel.store.set_roles(
            self.panel.guild_id, self.panel.role_capability, role_ids
        )
        await self.panel.replace(interaction)


class AICapabilitySelect(discord.ui.Select):
    def __init__(self, panel: "AISettingsView", policy: GuildAIPolicy):
        self.panel = panel
        options = [
            discord.SelectOption(
                label=AI_CAPABILITY_LABELS[name],
                value=name,
                default=policy.capability_enabled(name),
            )
            for name in AI_CAPABILITIES
        ]
        super().__init__(
            placeholder="Bật/tắt khả năng AI",
            options=options,
            min_values=0,
            max_values=len(options),
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        enabled = set(self.values)
        await self.panel.store.update(
            self.panel.guild_id,
            interaction.user.id,
            **{f"{name}_enabled": name in enabled for name in AI_CAPABILITIES},
        )
        await self.panel.replace(interaction)


class AILimitsModal(discord.ui.Modal, title="Giới hạn AI của server"):
    cooldown = discord.ui.TextInput(
        label="Cooldown mỗi người (0-300 giây)",
        max_length=3,
    )
    concurrent = discord.ui.TextInput(
        label=f"Số câu chạy cùng lúc (1-{GLOBAL_MAX_CONCURRENT})",
        max_length=2,
    )
    heavy_cooldown = discord.ui.TextInput(
        label="Cooldown tác vụ nặng (0-900 giây)",
        max_length=3,
    )
    video_seconds = discord.ui.TextInput(
        label=f"Video tối đa (5-{GLOBAL_MAX_VIDEO_SECONDS} giây)",
        max_length=4,
    )

    def __init__(self, panel: "AISettingsView", policy: GuildAIPolicy):
        super().__init__()
        self.panel = panel
        self.cooldown.default = str(policy.cooldown_seconds)
        self.concurrent.default = str(policy.max_concurrent)
        self.heavy_cooldown.default = str(policy.heavy_cooldown_seconds)
        self.video_seconds.default = str(policy.max_video_seconds)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            values = {
                "cooldown_seconds": int(self.cooldown.value),
                "max_concurrent": int(self.concurrent.value),
                "heavy_cooldown_seconds": int(self.heavy_cooldown.value),
                "max_video_seconds": int(self.video_seconds.value),
            }
        except ValueError:
            return await interaction.response.send_message(
                "❌ Các giới hạn phải là số nguyên.", ephemeral=True
            )
        await self.panel.store.update(
            self.panel.guild_id, interaction.user.id, **values
        )
        await self.panel.replace(interaction)


class AISettingsView(discord.ui.View):
    def __init__(
        self,
        cog: "Settings",
        guild_id: int,
        user_id: int,
        role_capability: str,
        policy: GuildAIPolicy,
        channels: set[int],
        roles: set[int],
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.store = cog.ai_store
        self.guild_id = int(guild_id)
        self.user_id = int(user_id)
        self.role_capability = role_capability
        self.policy = policy
        self.channels = channels
        self.roles = roles
        self.add_item(AIResponseModeSelect(self, policy))
        self.add_item(AIChannelSelect(self))
        self.add_item(AIRoleSelect(self))
        self.add_item(AICapabilitySelect(self, policy))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Hãy mở bảng `/settings ai` của riêng bạn.", ephemeral=True
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
        mode_labels = {
            "mention": "Chỉ mention/reply",
            "channels": "Chat tự nhiên trong kênh đã chọn",
            "off": "Đã tắt trong server",
        }
        channel_text = (
            ", ".join(f"<#{channel_id}>" for channel_id in sorted(self.channels))
            if self.channels
            else "Mọi kênh khi mention/reply; chat tự nhiên chưa có kênh"
        )
        role_text = (
            ", ".join(f"<@&{role_id}>" for role_id in sorted(self.roles))
            if self.roles
            else "Không giới hạn role"
        )
        enabled = [
            AI_CAPABILITY_LABELS[name]
            for name in AI_CAPABILITIES
            if self.policy.capability_enabled(name)
        ]
        disabled = [
            AI_CAPABILITY_LABELS[name]
            for name in AI_CAPABILITIES
            if not self.policy.capability_enabled(name)
        ]
        embed = discord.Embed(
            title="🤖 Cấu hình AI của Peto",
            description=(
                f"**Phản hồi:** {mode_labels[self.policy.response_mode]}\n"
                f"**Kênh AI:** {channel_text}\n"
                f"**Role cho {AI_ROLE_LABELS[self.role_capability]}:** {role_text}\n\n"
                f"**Đang bật:** {', '.join(enabled) or 'Không có'}\n"
                f"**Đang tắt:** {', '.join(disabled) or 'Không có'}\n\n"
                f"**Chống spam:** {self.policy.cooldown_seconds}s/người · "
                f"{self.policy.heavy_cooldown_seconds}s/tác vụ nặng · "
                f"{self.policy.max_concurrent} câu cùng lúc/server · "
                f"video tối đa {self.policy.max_video_seconds}s"
            ),
            color=0x57F287 if self.policy.response_mode != "off" else 0xED4245,
        )
        embed.set_footer(
            text="Chọn capability của role ngay trong tham số lệnh /settings ai."
        )
        return embed

    async def replace(self, interaction: discord.Interaction) -> None:
        view = await self.cog.create_ai_view(
            self.guild_id, self.user_id, self.role_capability
        )
        await interaction.response.edit_message(embed=await view.build_embed(), view=view)

    @discord.ui.button(label="Giới hạn", emoji="⏱️", style=discord.ButtonStyle.primary, row=4)
    async def limits(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(AILimitsModal(self, self.policy))

    @discord.ui.button(label="Bỏ giới hạn kênh", emoji="🧹", style=discord.ButtonStyle.secondary, row=4)
    async def clear_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self.store.set_channels(self.guild_id, [])
        await self.replace(interaction)

    @discord.ui.button(label="Bỏ giới hạn role", emoji="👥", style=discord.ButtonStyle.secondary, row=4)
    async def clear_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self.store.set_roles(self.guild_id, self.role_capability, [])
        await self.replace(interaction)

class Settings(commands.Cog):
    settings = app_commands.Group(
        name="settings",
        description="Cấu hình Peto riêng cho server này",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = GuildSettingsStore()
        self.ai_store = GuildAISettingsStore()
        self._ai_guilds_seeded = False

    async def cog_load(self) -> None:
        await self.store.init()
        await self.ai_store.init()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._ai_guilds_seeded:
            return
        await self.ai_store.seed_existing_guilds([int(guild.id) for guild in self.bot.guilds])
        self._ai_guilds_seeded = True

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self._ai_guilds_seeded:
            await self.ai_store.ensure(int(guild.id), legacy=False)

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

    async def create_ai_view(
        self,
        guild_id: int,
        user_id: int,
        role_capability: str,
    ) -> AISettingsView:
        policy = await self.ai_store.ensure(guild_id)
        channels = await self.ai_store.list_channels(guild_id)
        roles = await self.ai_store.list_roles(guild_id, role_capability)
        return AISettingsView(
            self,
            guild_id,
            user_id,
            role_capability,
            policy,
            channels,
            roles,
        )

    @settings.command(
        name="ai",
        description="Cấu hình quyền, khả năng và chống spam của Peto AI",
    )
    @app_commands.describe(
        capability="Nhóm tính năng cần chọn role được phép sử dụng",
    )
    @app_commands.choices(
        capability=[
            app_commands.Choice(name=AI_ROLE_LABELS[value], value=value)
            for value in ROLE_CAPABILITIES
        ]
    )
    async def ai(
        self,
        interaction: discord.Interaction,
        capability: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await self._allowed(interaction):
            return
        role_capability = capability.value if capability else "chat"
        view = await self.create_ai_view(
            int(interaction.guild_id), interaction.user.id, role_capability
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
