import math
import random

import discord
from discord import app_commands
from discord.ext import commands

from music.state import get_guild_state


def format_duration(seconds):
    if not seconds:
        return "Unknown"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def same_voice(interaction: discord.Interaction) -> bool:
    vc = interaction.guild.voice_client if interaction.guild else None
    return bool(vc and interaction.user.voice and interaction.user.voice.channel == vc.channel)


class QueuePagination(discord.ui.View):
    def __init__(self, original_interaction, queue_list, current_song):
        super().__init__(timeout=120)
        self.owner_id = original_interaction.user.id
        self.queue_list = list(queue_list)
        self.current_song = current_song
        self.page = 1
        self.per_page = 10
        self.total_pages = max(1, math.ceil(len(self.queue_list) / self.per_page))
        self.update_buttons()

    def update_buttons(self):
        self.children[0].disabled = self.page == 1
        self.children[1].disabled = self.page == self.total_pages

    def build_embed(self):
        embed = discord.Embed(title="🎶 Danh sách hàng đợi", color=0x2B2D31)
        if self.current_song:
            embed.add_field(name="▶️ Đang phát", value=f"**{self.current_song.get('title', 'Unknown')}** `[{format_duration(self.current_song.get('duration'))}]`", inline=False)
        if not self.queue_list:
            embed.description = "Hàng đợi đang trống."
            return embed
        total = sum(song.get("duration") or 0 for song in self.queue_list)
        start = (self.page - 1) * self.per_page
        lines = [f"`{i}.` **{song.get('title', 'Unknown')}** `[{format_duration(song.get('duration'))}]`" for i, song in enumerate(self.queue_list[start:start + self.per_page], start=start + 1)]
        embed.description = f"**{len(self.queue_list)}** bài • `{format_duration(total)}`\n\n" + "\n".join(lines)
        embed.set_footer(text=f"Trang {self.page}/{self.total_pages} • /remove, /move, /shuffle, /clear")
        return embed

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Chỉ người mở danh sách mới có thể lật trang.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀ Trước", style=discord.ButtonStyle.primary)
    async def previous(self, interaction, _button):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Sau ▶", style=discord.ButtonStyle.primary)
    async def following(self, interaction, _button):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class QueueCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def require_voice(self, interaction):
        if not same_voice(interaction):
            await interaction.response.send_message("❌ Bạn cần ở cùng voice channel với bot.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="queue", description="Hiển thị danh sách bài hát đang chờ")
    async def queue_cmd(self, interaction: discord.Interaction):
        state = get_guild_state(interaction.guild)
        current = state.history[-1] if state.history else None
        if not state.queue and not current:
            return await interaction.response.send_message("❌ Hàng đợi hiện đang trống!", ephemeral=True)
        view = QueuePagination(interaction, state.queue, current)
        await interaction.response.send_message(embed=view.build_embed(), view=view)

    @app_commands.command(name="remove", description="Xóa một bài khỏi hàng đợi")
    @app_commands.describe(position="Số thứ tự hiển thị trong /queue")
    async def remove(self, interaction: discord.Interaction, position: app_commands.Range[int, 1]):
        if not await self.require_voice(interaction): return
        state = get_guild_state(interaction.guild)
        items = list(state.queue)
        if position > len(items):
            return await interaction.response.send_message("❌ Vị trí không tồn tại trong hàng đợi.", ephemeral=True)
        song = items.pop(position - 1)
        state.queue.clear(); state.queue.extend(items)
        await interaction.response.send_message(f"🗑️ Đã xóa **{song.get('title', 'Unknown')}**.")

    @app_commands.command(name="move", description="Di chuyển bài trong hàng đợi")
    async def move(self, interaction: discord.Interaction, from_position: app_commands.Range[int, 1], to_position: app_commands.Range[int, 1]):
        if not await self.require_voice(interaction): return
        state = get_guild_state(interaction.guild); items = list(state.queue)
        if from_position > len(items) or to_position > len(items):
            return await interaction.response.send_message("❌ Vị trí không tồn tại trong hàng đợi.", ephemeral=True)
        song = items.pop(from_position - 1); items.insert(to_position - 1, song)
        state.queue.clear(); state.queue.extend(items)
        await interaction.response.send_message(f"↕️ Đã chuyển **{song.get('title', 'Unknown')}** tới vị trí `{to_position}`.")

    @app_commands.command(name="shuffle", description="Xáo trộn hàng đợi")
    async def shuffle(self, interaction: discord.Interaction):
        if not await self.require_voice(interaction): return
        state = get_guild_state(interaction.guild); items = list(state.queue)
        if len(items) < 2:
            return await interaction.response.send_message("❌ Cần ít nhất 2 bài để xáo trộn.", ephemeral=True)
        random.shuffle(items); state.queue.clear(); state.queue.extend(items)
        await interaction.response.send_message(f"🔀 Đã xáo trộn **{len(items)} bài**.")

    @app_commands.command(name="clear", description="Xóa toàn bộ hàng đợi, không dừng bài hiện tại")
    async def clear(self, interaction: discord.Interaction):
        if not await self.require_voice(interaction): return
        state = get_guild_state(interaction.guild); count = len(state.queue); state.queue.clear()
        await interaction.response.send_message(f"🧹 Đã xóa **{count} bài** khỏi hàng đợi.")


async def setup(bot):
    await bot.add_cog(QueueCommand(bot))
