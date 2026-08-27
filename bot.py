# bot.py
import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import logging
import os
import random
import config

from logging_setup import configure_logging
from music.state import get_guild_state, remove_guild_state


logger = logging.getLogger(__name__)


class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.members = os.getenv("WELCOME_ENABLED", "false").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

        super().__init__(
            command_prefix="!",
            intents=intents
        )

        self.status_list = [
            "Roblox",
            "Honkai: Star Rail",
            "Brown Dust 2",
            "Trickcal RE:VIVE",
            "Girls' Frontline 2: Exilium",
            "Minecraft",
            "Blue Archive",
            "Umamusume: Pretty Derby",
        ]
        self.current_status = None
        self.last_presence_warning_at = 0.0

    async def setup_hook(self):
        # commands/  -> slash command thật (/play, /skip...)
        # features/  -> Cog dạng lắng nghe sự kiện, không phải slash command
        #                (vd: ai_chat.py = Grok/SuperGrok xử lý on_message)
        extension_folders = ["commands", "features"]
        for folder in extension_folders:
            if not os.path.isdir(f"./{folder}"):
                continue
            for file in os.listdir(f"./{folder}"):
                if file.endswith(".py") and not file.startswith("_"):
                    await self.load_extension(f"{folder}.{file[:-3]}")

        await self.tree.sync()
        logger.info("Đã đồng bộ slash commands")

        self.rotate_status.start()

    @tasks.loop(hours=4)
    async def rotate_status(self):
        choices = [
            status for status in self.status_list
            if status != self.current_status
        ]
        selected_status = random.choice(choices or self.status_list)

        try:
            await self.change_presence(
                status=discord.Status.online,
                activity=discord.Game(name=selected_status),
            )
        except (
            aiohttp.ClientConnectionError,
            discord.ConnectionClosed,
            ConnectionResetError,
        ) as error:
            now = asyncio.get_running_loop().time()
            if now - self.last_presence_warning_at >= 60:
                logger.warning(
                    "Tạm bỏ qua cập nhật trạng thái vì Discord đang reconnect: %s",
                    error,
                )
                self.last_presence_warning_at = now
            return
        except Exception:
            logger.exception("Không thể cập nhật trạng thái Discord")
            return

        self.current_status = selected_status

    @rotate_status.before_loop
    async def before_rotate_status(self):
        await self.wait_until_ready()

    async def on_voice_state_update(self, member, before, after):
        if member != self.user:
            return

        state = get_guild_state(member.guild)

        # Bot đã rời voice channel.
        if before.channel is not None and after.channel is None:
            state.queue.clear()
            state.autoplay = False
            state.loop_mode = "off"
            state.cancel_idle_task()

            msg = state.now_playing_message
            state.now_playing_message = None
            if msg:
                try:
                    await msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
            return

        # Bot được kéo sang voice channel khác.
        if (
            before.channel is None
            or after.channel is None
            or before.channel.id == after.channel.id
        ):
            return

        try:
            from music.controls import (
                MusicControl,
                create_radio_panel,
                send_panel_message,
            )

            vc = member.guild.voice_client

            old_msg = state.now_playing_message
            state.now_playing_message = None
            if old_msg:
                try:
                    await old_msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

            new_channel = after.channel
            state.text_channel = new_channel

            if not vc or not (vc.is_playing() or vc.is_paused()) or not state.history:
                return

            current_song = state.history[-1]
            is_radio = current_song.get("source") == "radio"
            requester = current_song.get("requester")
            requester_mention = requester.mention if requester else "Autoplay"
            if is_radio:
                view = create_radio_panel(current_song, requester_mention)
            else:
                view = MusicControl(
                    vc,
                    track=current_song,
                    queue_length=len(state.queue),
                    requester_mention=requester_mention,
                )
            try:
                state.now_playing_message = await send_panel_message(new_channel, view)
            except discord.Forbidden:
                logger.warning(
                    "❌ Tôi không có quyền gửi tin nhắn ở voice channel "
                    "%s",
                    new_channel.name,
                )
        except Exception as error:
            logger.exception("Lỗi khi di chuyển Music Panel: %s", error)

    async def on_guild_remove(self, guild: discord.Guild):
        remove_guild_state(guild)


def main():
    configure_logging()
    bot = MusicBot()
    bot.run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
