import asyncio
import math

import discord
from discord import app_commands
from discord.ext import commands

from cache_manager import is_cache_build_active


def _milliseconds(value):
    """Đổi giây sang ms và loại bỏ giá trị nan/inf khi chưa có heartbeat."""
    try:
        milliseconds = float(value) * 1000
    except (TypeError, ValueError):
        return None
    return round(milliseconds) if math.isfinite(milliseconds) else None


def _grade_latency(milliseconds, good_limit, warning_limit):
    if milliseconds is None:
        return "⚪ Chưa có dữ liệu", 0
    if milliseconds < good_limit:
        return "🟢 Tốt", 0
    if milliseconds < warning_limit:
        return "🟡 Cần theo dõi", 1
    return "🔴 Cao", 2


def _format_ms(milliseconds):
    return f"{milliseconds} ms" if milliseconds is not None else "Đang đo..."


async def _measure_event_loop(samples=5, interval=0.05):
    """Đo thời gian event loop thức dậy trễ hơn lịch dự kiến."""
    loop = asyncio.get_running_loop()
    delays = []
    for _ in range(samples):
        started_at = loop.time()
        await asyncio.sleep(interval)
        elapsed = loop.time() - started_at
        delays.append(max(0.0, elapsed - interval) * 1000)
    return round(sum(delays) / len(delays), 1), round(max(delays), 1)


def _audio_pipeline_status(vc):
    if not vc or not vc.is_connected():
        return "⚪ Bot chưa kết nối voice"

    if vc.is_paused():
        playback = "⏸️ Đang tạm dừng"
    elif vc.is_playing():
        playback = "▶️ Đang phát"
    else:
        playback = "⏹️ Đang chờ"

    source = getattr(vc, "source", None)
    if source is None:
        source_mode = "Chưa có nguồn âm thanh"
    else:
        try:
            source_mode = "Opus trực tiếp" if source.is_opus() else "PCM/stream"
        except Exception:
            source_mode = "Không xác định"

    cache_active = is_cache_build_active()
    cache_status = "Đang xử lý" if cache_active else "Đang rảnh"
    cache_icon = "🟡" if cache_active else "🟢"
    return f"{playback} • `{source_mode}`\n{cache_icon} Tạo cache: **{cache_status}**"


class LatencyCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="latency", description="📶 Kiểm tra kết nối và sức khỏe voice")
    async def latency_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        gateway_ms = _milliseconds(self.bot.latency)
        gateway_status, gateway_level = _grade_latency(gateway_ms, 100, 200)

        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.is_connected():
            voice_ms = _milliseconds(vc.latency)
            voice_average_ms = _milliseconds(vc.average_latency)
            voice_status, voice_level = _grade_latency(voice_average_ms, 100, 180)
            voice_value = (
                f"Hiện tại: `{_format_ms(voice_ms)}`\n"
                f"Trung bình 20 nhịp: `{_format_ms(voice_average_ms)}`\n"
                f"{voice_status}"
            )
        else:
            voice_level = 0
            voice_value = "⚪ Bot chưa kết nối vào kênh voice."

        loop_average_ms, loop_max_ms = await _measure_event_loop()
        loop_status, loop_level = _grade_latency(loop_max_ms, 20, 50)

        if vc and vc.is_connected():
            audio_level = max(voice_level, loop_level)
            if audio_level == 0:
                audio_summary = "🟢 Âm thanh đang ổn định"
                color = discord.Color.green()
            elif audio_level == 1:
                audio_summary = "🟡 Âm thanh có dao động nhẹ"
                color = discord.Color.gold()
            else:
                audio_summary = "🔴 Âm thanh có độ trễ cao"
                color = discord.Color.red()
        else:
            audio_summary = "⚪ Chưa thể đánh giá âm thanh vì bot chưa vào voice"
            color = discord.Color.gold() if gateway_level else discord.Color.light_grey()

        gateway_summaries = {
            0: "🟢 Gateway ổn định",
            1: "🟡 Gateway có dao động",
            2: "🔴 Gateway cao",
        }
        overall = f"{audio_summary}\n{gateway_summaries[gateway_level]}"

        embed = discord.Embed(
            title="📶 Voice Health",
            description=overall,
            color=color,
        )
        embed.add_field(
            name="🌐 Discord Gateway",
            value=f"`{_format_ms(gateway_ms)}`\n{gateway_status}",
            inline=True,
        )
        embed.add_field(
            name="🎙️ Voice",
            value=voice_value,
            inline=True,
        )
        embed.add_field(
            name="⚙️ Event loop",
            value=(
                f"Trung bình: `{loop_average_ms} ms`\n"
                f"Cao nhất: `{loop_max_ms} ms`\n"
                f"{loop_status}"
            ),
            inline=True,
        )
        embed.add_field(
            name="🎵 Audio pipeline",
            value=_audio_pipeline_status(vc),
            inline=False,
        )
        embed.add_field(
            name="🧭 Cách đọc nhanh",
            value=(
                "Voice tăng cao nhưng Gateway ổn → nghiêng về đường truyền voice.\n"
                "Event loop tăng cao → máy bot hoặc tác vụ nền đang bị nghẽn.\n"
                "Chỉ Gateway cao → lệnh có thể chậm, chưa đủ kết luận nhạc bị giật."
            ),
            inline=False,
        )
        embed.set_footer(
            text="Voice ping là heartbeat của voice WebSocket; packet loss UDP có thể không hiện trực tiếp."
        )

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(LatencyCommand(bot))
