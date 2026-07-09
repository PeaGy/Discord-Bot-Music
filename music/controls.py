import discord
import aiohttp
import urllib.parse
import re

class MusicControl(discord.ui.View):
    def __init__(self, vc):
        super().__init__(timeout=None)
        self.vc = vc

    # 🔒 HÀM BẢO MẬT: Kiểm tra xem người bấm có ở chung phòng Voice không
    async def check_voice(self, interaction: discord.Interaction):
        if not interaction.user.voice or interaction.user.voice.channel.id != self.vc.channel.id:
            await interaction.response.send_message("❌ **Bạn phải ở cùng kênh Voice với mình mới điều khiển được nhé!**", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Back", emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        from music.player import history, queue, play_next

        if len(history) < 2:
            return await interaction.response.send_message("❌ Không có bài hát trước đó", ephemeral=True)

        current_song = history.pop()
        previous_song = history.pop()

        queue.appendleft(current_song)
        queue.appendleft(previous_song)

        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.is_previous_action = True
            self.vc.stop()
        else:
            await play_next(interaction.client, self.vc, interaction.channel)

        await interaction.response.defer()

    @discord.ui.button(label="Pause", emoji="⏸️", style=discord.ButtonStyle.secondary)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        
        if self.vc.is_playing():
            self.vc.pause()
            button.label = "Resume"
            button.emoji = discord.PartialEmoji.from_str("▶️")
            button.style = discord.ButtonStyle.success # Đổi sang màu Xanh khi tạm dừng
        else:
            self.vc.resume()
            button.label = "Pause"
            button.emoji = discord.PartialEmoji.from_str("⏸️")
            button.style = discord.ButtonStyle.secondary # Trở lại màu xám khi phát

        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger) # Đổi nút Stop thành màu Đỏ
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        from music.player import queue, now_playing_messages

        msg = now_playing_messages.pop(interaction.guild.id, None)
        queue.clear()

        # Vô hiệu hóa tất cả các nút khi bot dừng
        for child in self.children:
            child.disabled = True

        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.stop_request = True
            self.vc.stop()

        if self.vc.is_connected():
            await self.vc.disconnect()

        if msg:
            embed = discord.Embed(description="⏹️ **Đã dừng phát nhạc và rời kênh**", color=0x2b2d31)
            try:
                await msg.edit(embed=embed, view=self) # Cập nhật view đã disabled
            except:
                pass

        await interaction.response.defer()

    @discord.ui.button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        
        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.skip_request = True
            self.vc.stop()

        await interaction.response.defer()

    @discord.ui.button(label="Loop", emoji="🔂", style=discord.ButtonStyle.secondary)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        bot = interaction.client
        
        if not hasattr(bot, "looping"):
            bot.looping = False

        bot.looping = not bot.looping
        
        # Cập nhật màu sắc nút thay vì gửi tin nhắn rác
        button.style = discord.ButtonStyle.success if bot.looping else discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("✅ **Bật lặp lại**" if bot.looping else "❌ **Tắt lặp lại**", ephemeral=True)

    @discord.ui.button(label="Autoplay", emoji="🔀", style=discord.ButtonStyle.secondary)
    async def autoplay(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        from music.player import autoplay_guilds

        guild_id = interaction.guild.id

        if guild_id in autoplay_guilds:
            autoplay_guilds.remove(guild_id)
            button.style = discord.ButtonStyle.secondary
            msg = "❌ **Tắt Autoplay**"
        else:
            autoplay_guilds.add(guild_id)
            button.style = discord.ButtonStyle.success
            msg = "✅ **Bật Autoplay**"

        # Cập nhật trạng thái màu của nút
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Lyric", emoji="📝", style=discord.ButtonStyle.secondary)
    async def lyric(self, interaction: discord.Interaction, button: discord.ui.Button):
        from music.player import history

        vc = self.vc
        if not vc or not (vc.is_playing() or vc.is_paused()):
            embed = discord.Embed(description="Không có nhạc nào đang phát", color=0x2b2d31)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        if len(history) == 0:
            embed = discord.Embed(description="Không có thông tin bài hát", color=0x2b2d31)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        current_song = history[-1]
        title = current_song.get("title", "Unknown")
        artist = current_song.get("author", "")
        
        await interaction.response.defer(ephemeral=True)
        
        clean_title = re.sub(r'\(.*?\)|\[.*?\]', '', title)
        clean_title = re.sub(r'(?i)(official|music video|lyric video|audio|video)', '', clean_title)
        clean_title = " ".join(clean_title.split())
        if not clean_title:
            clean_title = title
            
        clean_artist = ""
        if artist and artist != "Unknown":
            clean_artist = re.sub(r'(?i)(official|vevo|topic|- topic)', '', artist)
            clean_artist = " ".join(clean_artist.split())
            
        search_query = f"{clean_title} {clean_artist}".strip()
        query = urllib.parse.quote(search_query)
        url = f"https://lrclib.net/api/search?q={query}"
        
        lyrics = None
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data:
                            lyrics = data[0].get("syncedLyrics") or data[0].get("plainLyrics")
            except Exception:
                pass
                
        if not lyrics:
            embed = discord.Embed(description=f"❌ Không tìm thấy lời bài hát cho **{title}**.", color=0x2b2d31)
            return await interaction.followup.send(embed=embed, ephemeral=True)
            
        if len(lyrics) > 4000:
            lyrics = lyrics[:3997] + "..."
            
        embed = discord.Embed(
            title=f"🗣️ Lyrics: {title}",
            description=lyrics,
            color=0x2b2d31
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class RadioControl(discord.ui.View):
    def __init__(self, vc):
        super().__init__(timeout=None)
        self.vc = vc

    async def check_voice(self, interaction: discord.Interaction):
        if not interaction.user.voice or interaction.user.voice.channel.id != self.vc.channel.id:
            await interaction.response.send_message("❌ **Bạn phải ở cùng kênh Voice với mình mới điều khiển được nhé!**", ephemeral=True)
            return False
        return True
        
    @discord.ui.button(label="Down", emoji="🔉", style=discord.ButtonStyle.secondary, row=0)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        
        if self.vc.source and isinstance(self.vc.source, discord.PCMVolumeTransformer):
            self.vc.source.volume = max(0.0, self.vc.source.volume - 0.1)
            self.vc.current_volume = self.vc.source.volume
            await interaction.response.send_message(f"🔉 Âm lượng: **{int(self.vc.source.volume * 100)}%**", ephemeral=True)
        else:
            await interaction.response.send_message("Volume control not available", ephemeral=True)

    @discord.ui.button(label="Pause", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        
        if self.vc.is_playing():
            self.vc.pause()
            button.label = "Resume"
            button.emoji = discord.PartialEmoji.from_str("▶️")
            button.style = discord.ButtonStyle.success
        else:
            self.vc.resume()
            button.label = "Pause"
            button.emoji = discord.PartialEmoji.from_str("⏸️")
            button.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        from music.player import queue, now_playing_messages
        
        msg = now_playing_messages.pop(interaction.guild.id, None)
        queue.clear()
        
        for child in self.children:
            child.disabled = True

        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.stop_request = True
            self.vc.stop()
            
        if self.vc.is_connected():
            await self.vc.disconnect()
            
        if msg:
            embed = discord.Embed(description="⏹️ **Đã dừng phát Radio**", color=0x2b2d31)
            try:
                await msg.edit(embed=embed, view=self)
            except:
                pass
                
        await interaction.response.defer()

    @discord.ui.button(label="Up", emoji="🔊", style=discord.ButtonStyle.secondary, row=0)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        
        if self.vc.source and isinstance(self.vc.source, discord.PCMVolumeTransformer):
            self.vc.source.volume = min(2.0, self.vc.source.volume + 0.1)
            self.vc.current_volume = self.vc.source.volume
            await interaction.response.send_message(f"🔊 Âm lượng: **{int(self.vc.source.volume * 100)}%**", ephemeral=True)
        else:
            await interaction.response.send_message("Volume control not available", ephemeral=True)

    @discord.ui.button(label="Change", emoji="↪️", style=discord.ButtonStyle.primary, row=1)
    async def change(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_voice(interaction): return
        await interaction.response.defer(ephemeral=True)
        
        from commands.radio import RadioView
        view = RadioView(interaction, is_change=True)
        await view.fetch_stations()
        
        if not view.stations:
            embed = discord.Embed(description="No radio stations found.", color=0x2b2d31)
            return await interaction.followup.send(embed=embed, ephemeral=True)
            
        view.update_components()
        await interaction.followup.send(embed=view.generate_embed(), view=view, ephemeral=True)