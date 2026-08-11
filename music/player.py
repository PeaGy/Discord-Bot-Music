import discord
import yt_dlp
import asyncio
import concurrent.futures
import logging
import random
import time
from music.controls import (
    MusicControl,
    create_error_panel,
    create_loading_panel,
    create_queue_ended_panel,
    create_radio_panel,
    edit_panel_message,
    send_panel_message,
)
from music.state import get_guild_state
from cache_manager import get_audio_source, preload_audio


logger = logging.getLogger(__name__)

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


def _schedule_from_audio_thread(bot, coroutine, *, guild_id, action):
    """Đưa coroutine từ audio thread về event loop và ghi lại lỗi nền."""
    try:
        future = asyncio.run_coroutine_threadsafe(coroutine, bot.loop)
    except RuntimeError as error:
        coroutine.close()
        logger.warning(
            "Không thể lên lịch %s (guild=%s): %s",
            action,
            guild_id,
            error,
        )
        return

    def report_result(done_future):
        try:
            done_future.result()
        except concurrent.futures.CancelledError:
            pass
        except Exception:
            logger.exception("Tác vụ %s thất bại (guild=%s)", action, guild_id)

    future.add_done_callback(report_result)


async def _record_recent_safely(guild_id: int, song: dict) -> None:
    try:
        import music_library

        await music_library.record_recent(guild_id, song)
    except Exception:
        logger.exception("Không thể lưu lịch sử nghe (guild=%s)", guild_id)

# ==============================
# ⏳ IDLE TIMER (3 Phut)
# ==============================
async def start_idle_timer(vc: discord.VoiceClient, channel: discord.TextChannel = None):
    guild = vc.guild
    state = get_guild_state(guild)

    if channel:
        state.text_channel = channel

    if state.idle_task and not state.idle_task.done():
        return

    async def idle_check():
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(180)

            if vc.is_connected() and not vc.is_playing() and not state.always_on:
                await vc.disconnect()
                logger.info("Đã rời voice do không hoạt động (guild=%s)", guild.id)

                send_channel = state.text_channel or guild.system_channel
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
                        logger.debug(
                            "Không có quyền gửi thông báo idle (guild=%s)",
                            guild.id,
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Idle timer thất bại (guild=%s)", guild.id)
        finally:
            if state.idle_task is current_task:
                state.idle_task = None

    state.idle_task = asyncio.create_task(idle_check())

def cancel_idle_timer(vc: discord.VoiceClient):
    get_guild_state(vc.guild).cancel_idle_task()

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

async def handle_autoplay(bot, vc, channel, song, guild_id, trigger_play=False):
    import re

    state = get_guild_state(guild_id)
    history = state.history
    queue = state.queue
    loop = bot.loop

    def fetch_autoplay_data():
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
            played_ids.append(video_id)

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
            opts["playlist_end"] = 20
            opts["noplaylist"] = False
            
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(mix_url, download=False)
                    entries = [e for e in info.get("entries", []) if e.get("id") not in played_ids and e.get("title")]
            except Exception as error:
                logger.debug("Không lấy được YouTube Mix: %s", error)
        
        if not entries:
            entries = fallback_autoplay()
            entries = [e for e in entries if e.get("id") not in played_ids]
            
        return entries

    try:
        entries = await loop.run_in_executor(None, fetch_autoplay_data)

        # Người dùng có thể tắt autoplay trong lúc yt-dlp đang tìm bài.
        if not state.autoplay:
            return

        if entries:
            picked = random.choice(entries[:5])
            
            thumb = None
            if picked.get("thumbnail"):
                thumb = picked.get("thumbnail")
            elif picked.get("thumbnails") and len(picked["thumbnails"]) > 0:
                thumb = picked["thumbnails"][0]["url"]
                
            next_song = {
                "title": picked.get("title"),
                "author": picked.get("uploader") or picked.get("channel") or "Unknown",
                "url": picked.get("url") or picked.get("webpage_url"),
                "duration": picked.get("duration"),
                "thumbnail": thumb,
                "requester": None,
                "source": "youtube",
            }
            
            # ĐƯA VÀO QUEUE ĐỂ TIÊN TRI
            if not queue:
                queue.append(next_song)
                logger.info(
                    "Autoplay chọn bài %s (guild=%s)",
                    next_song["title"],
                    guild_id,
                )
                
                # KHỞI ĐỘNG TẢI NGẦM LUÔN!
                duration = int(next_song.get("duration") or 0)
                if duration <= 600:
                    from cache_manager import preload_audio
                    await preload_audio(next_song['url'])
                
                # Nếu bot đang dừng (do user skip quá nhanh hoặc API chậm) -> Ép phát luôn
                if not vc.is_playing() and not vc.is_paused():
                    trigger_play = True
                    
                if trigger_play:
                    await play_next(bot, vc, channel)
        else:
            logger.info("Autoplay không tìm thấy bài phù hợp (guild=%s)", guild_id)
            if trigger_play: await play_next(bot, vc, channel)
    except Exception as e:
        logger.exception("Autoplay thất bại (guild=%s): %s", guild_id, e)
        if trigger_play: await play_next(bot, vc, channel)
# ==============================
# 🎯 HÀM DÙNG CHUNG: THÊM BÀI + PHÁT (không phụ thuộc discord.Interaction)
# ==============================
# Được /play (commands/play.py) và Grok function calling
# (features/ai_chat.py) cùng gọi vào -> tránh lặp code, chỉ 1 nơi giữ
# logic thêm bài vào queue. Import trễ (bên trong hàm) để né circular import
# vì commands/play.py có import ngược lại từ music/player.py.
async def play_song_by_query(
    bot: discord.Client,
    guild: discord.Guild,
    voice_channel: discord.VoiceChannel,
    text_channel: discord.TextChannel,
    requester,
    query: str,
) -> dict:
    from commands.play import get_song_info, is_spotify_url
    from music.spotify import get_spotify_info
    from cache_manager import preload_audio

    vc = guild.voice_client
    if vc and vc.channel != voice_channel:
        return {"ok": False, "reason": f"Bot đang ở kênh voice khác: {vc.channel.name}"}

    if not vc:
        try:
            vc = await voice_channel.connect(self_deaf=True)
        except Exception as e:
            return {"ok": False, "reason": f"Không kết nối được voice: {e}"}

    loop = bot.loop
    try:
        if is_spotify_url(query):
            song = await loop.run_in_executor(None, get_spotify_info, query)
            if not song:
                return {"ok": False, "reason": "Không lấy được thông tin bài hát Spotify"}
        else:
            song = await loop.run_in_executor(None, get_song_info, query)
    except Exception as error:
        logger.warning(
            "Không lấy được metadata từ AI tool (guild=%s, query=%r): %s",
            guild.id,
            query,
            error,
        )
        if not vc.is_playing() and not vc.is_paused():
            await start_idle_timer(vc, channel=text_channel)
        return {"ok": False, "reason": "Không lấy được thông tin bài hát"}

    state = get_guild_state(guild)
    state.queue.append({**song, "requester": requester})

    duration = int(song.get("duration") or 0)
    is_radio = song.get("source") == "radio"
    if (vc.is_playing() or vc.is_paused()) and not is_radio and duration <= 600:
        asyncio.create_task(preload_audio(song["url"]))

    if not vc.is_playing() and not vc.is_paused():
        await play_next(bot, vc, text_channel)

    return {"ok": True, "song": song}


def skip_current(guild: discord.Guild) -> dict:
    vc = guild.voice_client
    if not vc or not (vc.is_playing() or vc.is_paused()):
        return {"ok": False, "reason": "Không có nhạc nào đang phát"}
    vc.skip_request = True
    vc.stop()
    return {"ok": True}


# ==============================
# ▶️ PLAY NEXT SONG
# ==============================
async def play_next(
    bot: discord.Client,
    vc: discord.VoiceClient,
    channel: discord.TextChannel,
):
    state = get_guild_state(vc.guild)
    async with state.play_lock:
        if not vc.is_connected():
            logger.info("Bỏ qua play_next vì voice đã ngắt (guild=%s)", vc.guild.id)
            return
        if vc.is_playing() or vc.is_paused():
            return
        await _play_next_locked(bot, vc, channel, state)


async def _play_next_locked(
    bot: discord.Client,
    vc: discord.VoiceClient,
    channel: discord.TextChannel,
    state,
):
    cancel_idle_timer(vc)
    queue = state.queue
    history = state.history
    state.text_channel = channel

    if not vc.is_connected():
        return

    # QUEUE HABIS
    if not queue:
        await start_idle_timer(vc, channel=channel)
        
        msg = state.now_playing_message
        state.now_playing_message = None
        if msg:
            try:
                await edit_panel_message(msg, create_queue_ended_panel(bot.user))
            except Exception as e:
                logger.debug("Không cập nhật được panel khi hết queue: %s", e)

        return

    song = queue.popleft()
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
    try:
        info = await loop.run_in_executor(None, extract_stream)
        if not info or not info.get("url"):
            raise ValueError("yt-dlp không trả về stream URL")
        source = info["url"]
    except Exception as error:
        logger.warning(
            "Không lấy được stream, bỏ qua bài %r (guild=%s): %s",
            song.get("title", "Unknown"),
            vc.guild.id,
            error,
        )
        return await _play_next_locked(bot, vc, channel, state)

    if "webpage_url" in info:
        song["url"] = info["webpage_url"]

    # ==============================
    # AFTER PLAYING CALLBACK
    # ==============================
    def after_playing(error):
        if error:
            logger.warning("Audio player báo lỗi (guild=%s): %s", vc.guild.id, error)

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
        loop_mode = state.loop_mode
        
        if loop_mode == "track" and not is_skip and not is_prev:
            # Lặp bài hiện tại: Nhét lại ngay lên đầu hàng đợi
            queue.appendleft(song)
            _schedule_from_audio_thread(
                bot,
                play_next(bot, vc, channel),
                guild_id=guild_id,
                action="lặp bài",
            )
            
        elif loop_mode == "queue" and not is_skip and not is_prev:
            # Lặp danh sách: Nhét bài vừa hát xong xuống cuối hàng đợi
            queue.append(song)
            _schedule_from_audio_thread(
                bot,
                play_next(bot, vc, channel),
                guild_id=guild_id,
                action="lặp queue",
            )

        # 🤖 AUTOPLAY MODE
        elif state.autoplay and not queue and not is_prev:
            _schedule_from_audio_thread(
                bot,
                handle_autoplay(bot, vc, channel, song, guild_id, trigger_play=True), 
                guild_id=guild_id,
                action="autoplay",
            )
        
        # ➡️ BÌNH THƯỜNG (CÒN QUEUE HOẶC SKIP)
        else:
            _schedule_from_audio_thread(
                bot,
                play_next(bot, vc, channel),
                guild_id=guild_id,
                action="phát bài kế tiếp",
            )
# ▶️ PLAY AUDIO
    # Lấy duration an toàn, thêm điều kiện > 10 phút (600 giây)
    duration = int(song.get("duration") or 0)
    is_radio = song.get("source") == "radio"
    is_too_long = duration > 600

    # ==============================
    # LOADING PANEL V2 (hiện ngay trong lúc tải/cache/normalize nhạc)
    # ==============================
    loading_msg = None
    if not is_radio and not is_too_long:
        loading_view = create_loading_panel(song)

        existing_msg = state.now_playing_message
        try:
            if existing_msg:
                await edit_panel_message(existing_msg, loading_view)
                loading_msg = existing_msg
            else:
                loading_msg = await send_panel_message(channel, loading_view)
        except Exception as e:
            logger.debug("Không thể hiện loading panel (guild=%s): %s", vc.guild.id, e)

        if loading_msg:
            state.now_playing_message = loading_msg

    try:
        # Nếu là Radio HOẶC Nhạc dài hơn 10 phút -> Phóng thẳng luồng Stream trực tiếp
        if is_radio or is_too_long:
            logger.info(
                "Stream trực tiếp %r, duration=%ss (guild=%s)",
                song.get("title"),
                duration,
                vc.guild.id,
            )
            base_source = discord.FFmpegPCMAudio(source, **FFMPEG_OPTIONS)
        else:
            logger.info(
                "Chuẩn bị cache %r, duration=%ss (guild=%s)",
                song.get("title"),
                duration,
                vc.guild.id,
            )
            base_source = await get_audio_source(song['url'])
    except Exception as e:
        logger.warning(
            "Không thể tạo nguồn phát %r (guild=%s): %s",
            song.get("title"),
            vc.guild.id,
            e,
        )
        if loading_msg:
            try:
                await edit_panel_message(loading_msg, create_error_panel(song))
            except Exception:
                pass
        return await _play_next_locked(bot, vc, channel, state)
    
    # Bọc qua VolumeTransformer
    audio_source = None
    try:
        audio_source = discord.PCMVolumeTransformer(base_source)
        audio_source.volume = getattr(vc, 'current_volume', 1.0)

        # Reset mốc thời gian
        vc.play_start_time = time.time()
        vc.total_paused_duration = 0
        vc.paused_at = None

        history.append(song)
        vc.play(
            audio_source,
            after=after_playing,
        )
        asyncio.create_task(_record_recent_safely(vc.guild.id, song))
    except Exception as error:
        if history and history[-1] is song:
            history.pop()
        try:
            (audio_source or base_source).cleanup()
        except Exception:
            pass
        logger.warning(
            "Không thể bắt đầu phát %r (guild=%s): %s",
            song.get("title"),
            vc.guild.id,
            error,
        )
        return await _play_next_locked(bot, vc, channel, state)

    # ==============================
    # ⚡ TÍNH NĂNG SMART PRE-CACHING
    # ==============================
    # Ngay khi bài hiện tại bắt đầu hát, tự động kiểm tra bài số 2 trong Queue
    if len(queue) > 0:
        # Nếu đã có bài sẵn trong hàng đợi -> Preload bài đó
        next_song = queue[0]
        next_duration = int(next_song.get("duration") or 0)
        next_is_radio = next_song.get("source") == "radio"
        if not next_is_radio and next_duration <= 600:
            from cache_manager import preload_audio
            asyncio.create_task(preload_audio(next_song['url']))
            
    elif state.autoplay:
        # Nếu hàng đợi trống trơn NHƯNG Autoplay đang bật 
        # -> Gọi handle_autoplay để nó tự tìm bài mới và Preload ngầm!
        asyncio.create_task(handle_autoplay(bot, vc, channel, song, vc.guild.id, trigger_play=False))
    # ==============================
    # NOW PLAYING COMPONENTS V2
    # ==============================
    try:
        requester_mention = requester.mention if requester else "Autoplay"

        if song.get("source") == "radio":
            view = create_radio_panel(song, requester_mention)
        else:
            view = MusicControl(
                vc,
                track=song,
                current_time=0,
                queue_length=len(queue),
                requester_mention=requester_mention,
            )

        # Tái sử dụng cùng một tin nhắn để không làm đầy kênh chat.
        existing_msg = state.now_playing_message
        
        if existing_msg:
            try:
                await edit_panel_message(existing_msg, view)
            except Exception as edit_err:
                logger.debug("Không edit được Music Panel: %s", edit_err)
                msg = await send_panel_message(channel, view)
                state.now_playing_message = msg
        else:
            msg = await send_panel_message(channel, view)
            state.now_playing_message = msg
            
    except Exception as e:
        logger.exception("Không thể tạo Music Panel (guild=%s): %s", vc.guild.id, e)
