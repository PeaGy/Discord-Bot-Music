import discord
import yt_dlp
import asyncio
import random
import time
from collections import deque
from music.controls import MusicControl
from cache_manager import get_audio_source

# ==============================
# GLOBAL STATE
# ==============================
queue = deque()
history = deque(maxlen=20)

idle_tasks = {}
always_on_guilds = set()
autoplay_guilds = set()
text_channels = {}
now_playing_messages = {}

# ==============================
# YTDLP & FFMPEG OPTIONS
# ==============================
YDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch",
    "cookies": "cookies.txt",  #cookies.txt file path
    "extractor_args": {"youtube": ["player_client=ios,android,web", "player_skip=webpage"]},
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
        "-reconnect_at_eof 1 -reconnect_on_network_error 1"
    ),
    # ⚠️ Đây là stream SỐNG (radio) -> không có file để đo loudness trước (2-pass
    # loudnorm không khả thi). Dùng dynaudnorm với cửa sổ dài (f=500) + giới hạn
    # gain thấp (m=10) để đỡ "bơm" âm lượng hơn nhiều so với loudnorm real-time
    # mặc định (loudnorm real-time là nguyên nhân chính gây pumping trước đây).
    "options": "-vn -af dynaudnorm=f=500:g=31:m=10:s=0",
}

# ==============================
# ⏳ IDLE TIMER (3 Phut)
# ==============================
async def start_idle_timer(vc: discord.VoiceClient, channel: discord.TextChannel = None):
    guild = vc.guild
    guild_id = guild.id

    if channel:
        text_channels[guild_id] = channel

    if guild_id in idle_tasks:
        return

    async def idle_check():
        await asyncio.sleep(180)

        if vc and not vc.is_playing() and guild_id not in always_on_guilds:
            await vc.disconnect()
            print(f"🔌 Disconnected from {guild.name}")

            send_channel = text_channels.get(guild_id) or guild.system_channel
            if not send_channel:
                for c in guild.text_channels:
                    if c.permissions_for(guild.me).send_messages:
                        send_channel = c
                        break

            if send_channel:
                embed = discord.Embed(
                    description="""Không bài nào được phát trong 3 phút, sủi đây 👋\n\nbạn có thể để tôi ở lại lâu hơn với command 247!"""
                )
                try:
                    await send_channel.send(embed=embed)
                except discord.Forbidden:
                    pass

        idle_tasks.pop(guild_id, None)

    idle_tasks[guild_id] = asyncio.create_task(idle_check())

def cancel_idle_timer(vc: discord.VoiceClient):
    task = idle_tasks.pop(vc.guild.id, None)
    if task:
        task.cancel()

# ==============================
# 🤖 AUTOPLAY QUERY BUILDER
# ==============================
def build_autoplay_query(song: dict) -> str:
    title = song.get("title", "")
    artist = title.split("-")[0]

    keywords = [
        artist.strip(),
        "official audio",
        "topic",
        "music",
    ]

    return " ".join(keywords)

async def handle_autoplay(bot, vc, channel, song, guild_id):
    from music.player import history, queue, autoplay_guilds

    loop = bot.loop

    def fetch_autoplay_data():
        import re
        # 1. Lấy danh sách các ID video đã phát trong History để không random trùng lại
        played_ids = []
        for h_song in history:
            h_url = h_song.get("url", "")
            h_match = re.search(r"(?:v=|/)([0-9A-Za-z_-]{11}).*", h_url)
            if h_match:
                played_ids.append(h_match.group(1))

        url = song.get("url", "")
        video_id = None
        match = re.search(r"(?:v=|/)([0-9A-Za-z_-]{11}).*", url)
        if match:
            video_id = match.group(1)
            played_ids.append(video_id) # Tránh lặp lại chính bài hiện tại

        def fallback_autoplay():
            query = build_autoplay_query(song)
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                res = ydl.extract_info(f"ytsearch5:{query} related music", download=False)
                return res.get("entries", [])

        entries = []
        if video_id:
            mix_url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
            opts = YDL_OPTIONS.copy()
            opts["extract_flat"] = True
            opts["playlist_end"] = 20 # Tăng lên 20 để có nhiều lựa chọn hơn sau khi lọc
            opts["noplaylist"] = False
            
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(mix_url, download=False)
                    # 2. Lọc: Bỏ qua những bài không có title VÀ những bài đã nằm trong history
                    entries = [e for e in info.get("entries", []) if e.get("id") not in played_ids and e.get("title")]
            except:
                pass
        
        if not entries:
            entries = fallback_autoplay()
            # Lọc lại history cho fallback
            entries = [e for e in entries if e.get("id") not in played_ids]
            
        return entries

    # 3. Đưa quá trình fetch yt-dlp vào executor để không làm lag bot
    try:
        entries = await loop.run_in_executor(None, fetch_autoplay_data)
        
        if entries:
            picked = random.choice(entries[:5])
            
            thumb = None
            if picked.get("thumbnail"):
                thumb = picked.get("thumbnail")
            elif picked.get("thumbnails") and len(picked["thumbnails"]) > 0:
                thumb = picked["thumbnails"][0]["url"]
                
            queue.append({
                "title": picked.get("title"),
                "author": picked.get("uploader") or picked.get("channel") or "Unknown",
                "url": picked.get("url") or picked.get("webpage_url"),
                "duration": picked.get("duration"),
                "thumbnail": thumb,
                "requester": None,
                "source": "youtube",
            })
        else:
            print("[AUTOPLAY] Failed: No new entries found (might be stuck in a loop).")
    except Exception as e:
        print(f"[AUTOPLAY ERROR]: {e}")

    # 4. GỌI NEXT SONG SAU KHI ĐÃ CÓ DATA
    await play_next(bot, vc, channel)
# ==============================
# ▶️ PLAY NEXT SONG
# ==============================
async def play_next(
    bot: discord.Client,
    vc: discord.VoiceClient,
    channel: discord.TextChannel,
):
    cancel_idle_timer(vc)

    # QUEUE HABIS
    if not queue:
        await start_idle_timer(vc, channel=channel)
        
        msg = now_playing_messages.pop(vc.guild.id, None)
        if msg:
            embed = discord.Embed(
                title="Queue Ended!",
                description="Tất cả nhạc đã được phát! You can add songs again\nusing `/play` or `/search` command.",
                color=0x2b2d31
            )
            if bot.user.display_avatar:
                embed.set_author(name=f"{bot.user.display_name} ✨", icon_url=bot.user.display_avatar.url)
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Đô nết me", url="https://www.facebook.com/peagy.simp.lo/", emoji="🤑"))
            
            try:
                await msg.edit(embed=embed, view=view)
            except Exception as e:
                print(f"Error editing queue ended message: {e}")

        return

    song = queue.popleft()
    history.append(song)
    requester = song.get("requester")

    # LẤY URL STREAM KHÔNG BỊ BLOCK LOOP
    def extract_stream():
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            query = song.get("search_query") or song["url"]
            if song.get("source") == "spotify" and not query.startswith("ytsearch"):
                query = f"ytsearch1:{query}"
                
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return info

    loop = bot.loop
    info = await loop.run_in_executor(None, extract_stream)
    
    source = info["url"]

    if "webpage_url" in info:
        song["url"] = info["webpage_url"]

    # ==============================
    # AFTER PLAYING CALLBACK
    # ==============================
    def after_playing(error):
        if error:
            print(f"Player error: {error}")

        if not vc or not vc.is_connected():
            return

        guild_id = vc.guild.id

        is_skip = getattr(vc, 'skip_request', False)
        is_prev = getattr(vc, 'is_previous_action', False)
        is_stop = getattr(vc, 'stop_request', False)

        if hasattr(vc, 'skip_request'): del vc.skip_request
        if hasattr(vc, 'is_previous_action'): del vc.is_previous_action
        if hasattr(vc, 'stop_request'): del vc.stop_request

        if is_stop:
            return

        # 🔁 LOOP MODE
        if getattr(bot, "looping", False) and not is_skip and not is_prev:
            queue.appendleft(song)
            asyncio.run_coroutine_threadsafe(play_next(bot, vc, channel), bot.loop)

        # 🤖 AUTOPLAY MODE
        elif guild_id in autoplay_guilds and not queue and not is_prev:
            # GỌI HÀM ASYNC Ở BACKGROUND (Không block luồng)
            asyncio.run_coroutine_threadsafe(
                handle_autoplay(bot, vc, channel, song, guild_id), 
                bot.loop
            )
        
        # ➡️ BÌNH THƯỜNG (CÒN QUEUE HOẶC SKIP)
        else:
            asyncio.run_coroutine_threadsafe(play_next(bot, vc, channel), bot.loop)

# ▶️ PLAY AUDIO
    # Lấy duration an toàn (tránh lỗi NoneType nếu yt_dlp không quét được thời gian)
    duration = int(song.get("duration") or 0)
    is_radio = song.get("source") == "radio"

    # ==============================
    # LOADING EMBED (hiện ngay trong lúc tải/cache/normalize nhạc)
    # ==============================
    loading_msg = None
    if not is_radio:
        loading_embed = discord.Embed(
            description=f"⏳ **Đang tải nhạc:** {song.get('title', 'Unknown')}\nVui lòng đợi chút xíu...",
            color=0x2b2d31
        )
        if song.get("thumbnail"):
            loading_embed.set_thumbnail(url=song["thumbnail"])

        existing_msg = now_playing_messages.get(vc.guild.id)
        try:
            if existing_msg:
                await existing_msg.edit(embed=loading_embed, view=None)
                loading_msg = existing_msg
            else:
                loading_msg = await channel.send(embed=loading_embed)
        except Exception as e:
            print(f"[DEBUG] Không thể hiện loading embed: {e}")

        if loading_msg:
            now_playing_messages[vc.guild.id] = loading_msg

    try:
        if is_radio:
            # SỬ DỤNG `source` (link stream gốc) THAY VÌ `song['url']` (link youtube HTML)
            # Chỉ radio sống mới đi thẳng qua network vì không thể cache một stream vô hạn.
            print(f"📡 [STREAMING DIRECT] Phát trực tiếp: {song.get('title')} ({duration}s)")
            base_source = discord.FFmpegPCMAudio(source, **FFMPEG_OPTIONS)
        else:
            # Luôn tải + normalize (2-pass loudnorm) trước khi phát, kể cả bài dài.
            # 2 lợi ích: (1) hết giật vì phát từ file local, không còn phụ thuộc
            # throttle của CDN YouTube giữa chừng; (2) loudness đều nhau giữa mọi
            # bài vì đều đi qua cùng một bước normalize trong cache_manager.py.
            print(f"💾 [CACHING] Đang tải + normalize cho: {song.get('title')} ({duration}s)")
            base_source = await get_audio_source(song['url'])
    except Exception as e:
        # Tải/normalize lỗi (mạng, video bị gỡ, cookies hết hạn...) -> báo lỗi
        # ngay trên chính embed loading, rồi tự động bỏ qua sang bài kế tiếp
        # thay vì làm cả play_next() crash và kẹt ở màn hình "Đang tải nhạc".
        print(f"[ERROR] Không thể tạo nguồn phát cho {song.get('title')}: {e}")
        if loading_msg:
            error_embed = discord.Embed(
                description=f"❌ **Không thể phát:** {song.get('title', 'Unknown')}\nBỏ qua bài này...",
                color=0xFF6B6B
            )
            try:
                await loading_msg.edit(embed=error_embed, view=None)
            except Exception:
                pass
        return await play_next(bot, vc, channel)
    
    # Bọc qua VolumeTransformer (giữ nguyên âm lượng đã chỉnh)
    audio_source = discord.PCMVolumeTransformer(base_source)
    audio_source.volume = getattr(vc, 'current_volume', 1.0)

    # Reset mốc thời gian phát cho bài mới (dùng để tính progress bar on-demand
    # trong controls.py -> get_current_time()
    vc.play_start_time = time.time()
    vc.total_paused_duration = 0
    vc.paused_at = None

    vc.play(
        audio_source,
        after=after_playing,
    )

    # ==============================
    # NOW PLAYING EMBED (GIAO DIỆN MỚI)
    # ==============================
    try:
        # 1. Kiểm tra xem có phải là Radio không
        if song.get("source") == "radio":
            try:
                from music.controls import RadioControl
                view = RadioControl(vc)
            except ImportError:
                view = None
                
            embed = discord.Embed(
                title="🍐 RADIO PANEL",
                description=f"**{song.get('title', 'Unknown')}**",
                color=0x2b2d31
            )
            if song.get("thumbnail"):
                embed.set_thumbnail(url=song["thumbnail"])
            embed.add_field(name="Requested By", value=requester.mention if requester else "Autoplay", inline=True)
            
        else:
            # 2. ÁP DỤNG GIAO DIỆN MỚI CHO MUSIC
            from music.controls import MusicControl
            
            # Khởi tạo View mới (Truyền session voice và thông tin bài hát vào)
            view = MusicControl(vc, track=song, current_time=0)
            
            # Gọi hàm sinh giao diện từ controls.py
            requester_mention = requester.mention if requester else "Autoplay"
            
            # Vì queue là biến Global, ta truyền thẳng len(queue) vào luôn
            embed = view.generate_embed(queue_length=len(queue), requester_mention=requester_mention)

        # 3. Logic gửi/chỉnh sửa tin nhắn
        existing_msg = now_playing_messages.get(vc.guild.id)
        
        if existing_msg:
            try:
                await existing_msg.edit(embed=embed, view=view)
            except Exception as edit_err:
                print(f"[DEBUG] Lỗi edit tin nhắn cũ: {edit_err}")
                msg = await channel.send(embed=embed, view=view)
                now_playing_messages[vc.guild.id] = msg
        else:
            msg = await channel.send(embed=embed, view=view)
            now_playing_messages[vc.guild.id] = msg
            
    except Exception as e:
        print(f"[ERROR] Lỗi không thể tạo bảng MUSIC PANEL: {e}")