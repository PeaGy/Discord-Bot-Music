import discord
from discord import app_commands
from discord.ext import commands
import time

class LatencyCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="latency", description="⏱️ Kiểm tra độ trễ (ping) của bot")
    async def latency_cmd(self, interaction: discord.Interaction):
        # Tính toán độ trễ từ heartbeat của bot (đơn vị là giây, chuyển sang mili giây)
        latency_ms = round(self.bot.latency * 1000)
        
        # Xác định trạng thái của bot dựa trên ping
        if latency_ms < 100:
            status = "🟢 Rất mượt"
            color = discord.Color.green()
        elif latency_ms < 200:
            status = "🟡 Bình thường"
            color = discord.Color.gold()
        else:
            status = "🔴 Hơi lag"
            color = discord.Color.red()

        embed = discord.Embed(
            title="📶 Trạng thái kết nối",
            description=f"**Ping:** `{latency_ms}ms`\n**Trạng thái:** {status}",
            color=color
        )
        embed.set_footer(text="Độ trễ này là giữa bot và máy chủ Discord.")
        
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(LatencyCommand(bot))