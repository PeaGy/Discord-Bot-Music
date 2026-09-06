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
from music.source_fallback import (
    get_cached_audius_track_id,
    get_cached_soundcloud_page,
    resolve_audio_fallback_sync,
)
from cache_manager import (
    get_audio_source,
    get_long_audio_source,
    preload_audio,
)
from ytdlp_support import (
    audio_fallback_enabled,
    audio_fallback_timeout_seconds,
    extract_info_with_retry,
    is_transient_ytdlp_error,
    should_use_long_audio_temp,
    youtube_ydl_options,
)


logger = logging.getLogger(__name__)

# ==============================
# YTDLP & FFMPEG OPTIONS
# ==============================
YDL_OPTIONS = youtube_ydl_options({
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch",
})

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


async def _record_play_event_safely(guild_id: int, song: dict, seconds: int, skipped: bool) -> None:
    try:
        import music_library
        await music_library.record_play_event(guild_id, song, seconds, skipped=skipped)
    except Exception:
        logger.exception("Không thể lưu thống kê nghe (guild=%s)", guild_id)

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


def _is_youtube_page_url(value: object) -> bool:
    url = str(value or "").strip().casefold()
    return bool(
        "youtube.com/watch" in url
        or "youtube.com/shorts/" in url
        or "youtu.be/" in url
    )


def _needs_stream_lookup(song: dict, *, use_direct_stream: bool) -> bool:
    """Only resolve a stream URL when the selected playback path needs one."""
    if use_direct_stream or song.get("source") == "spotify":
        return True
    if song.get("source") == "youtube":
        return not _is_youtube_page_url(song.get("url"))
    return False


def _youtube_entry_url(entry: dict) -> str | None:
    webpage_url = entry.get("webpage_url")
    if webpage_url:
        return webpage_url
    video_id = str(entry.get("id") or "").strip()
    if len(video_id) == 11:
        return f"https://www.youtube.com/watch?v={video_id}"
    raw_url = str(entry.get("url") or "").strip()
    if len(raw_url) == 11 and "/" not in raw_url:
        return f"https://www.youtube.com/watch?v={raw_url}"
    return raw_url or None


def _can_use_audio_fallback(song: dict, error: BaseException | None = None) -> bool:
    if not audio_fallback_enabled():
        return False
    if str(song.get("source") or "").casefold() not in {"youtube", "spotify"}:
        return False
    return error is None or is_transient_ytdlp_error(error)


def _can_use_soundcloud_fallback(song: dict, error: BaseException | None = None) -> bool:
    """Backwards-compatible name retained for callers outside this module."""
    return _can_use_audio_fallback(song, error)


def _cached_audio_fallback_hint(song: dict) -> tuple[str | None, str | None]:
    source = str(song.get("fallback_source") or "").casefold()
    locator = str(song.get("fallback_locator") or "").strip()
    if source == "soundcloud":
        return source, locator or str(song.get("fallback_url") or "").strip()
    if source == "audius" and locator:
        return source, locator

    soundcloud_page = get_cached_soundcloud_page(song)
    if soundcloud_page:
        return "soundcloud", soundcloud_page
    audius_track_id = get_cached_audius_track_id(song)
    if audius_track_id:
        return "audius", audius_track_id
    return None, None


async def _try_audio_fallback(
    song: dict,
    *,
    error: BaseException | None = None,
    preferred_source: str | None = None,
    preferred_locator: str | None = None,
) -> dict | None:
    """Resolve a verified SoundCloud/Audius match without audio caching."""
    if not _can_use_audio_fallback(song, error):
        return None

    logger.info(
        "Tìm audio fallback trực tiếp cho %r (youtube_error=%s)",
        song.get("title", "Unknown"),
        str(error) if error else "cached-match",
    )
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                resolve_audio_fallback_sync,
                song,
                preferred_source=preferred_source,
                preferred_locator=preferred_locator,
            ),
            timeout=audio_fallback_timeout_seconds(),
        )
    except TimeoutError:
        logger.warning("Audio fallback quá thời gian chờ cho %r", song.get("title"))
        return None
    except Exception as fallback_error:
        logger.warning(
            "Audio fallback thất bại cho %r: %s",
            song.get("title"),
            fallback_error,
        )
        return None
    if not result:
        logger.info("Không có audio fallback đủ khớp cho %r", song.get("title"))
        return None

    fallback_source = str(result.get("fallback_source") or "").casefold()
    song["fallback_source"] = fallback_source
    song["fallback_url"] = result["webpage_url"]
    song["fallback_locator"] = result.get("fallback_locator")
    song["stream_only"] = True
    logger.info(
        "Dùng %s fallback trực tiếp cho %r: %s",
        fallback_source.capitalize(),
        song.get("title", "Unknown"),
        result["webpage_url"],
    )
    return result


async def _try_soundcloud_fallback(
    song: dict,
    *,
    error: BaseException | None = None,
    preferred_page_url: str | None = None,
) -> dict | None:
    """Compatibility wrapper for older tests/extensions."""
    return await _try_audio_fallback(
        song,
        error=error,
        preferred_source="soundcloud" if preferred_page_url else None,
        preferred_locator=preferred_page_url,
    )

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
                "url": _youtube_entry_url(picked),
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
                    preload_delay = 3.0 if (vc.is_playing() or vc.is_paused()) else 0
                    await preload_audio(next_song['url'], delay=preload_delay)
                
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
    import music_library
    key = music_library.track_key(song)
    duplicate = next(
        (index for index, item in enumerate(state.queue, start=1)
         if music_library.track_key(item) == key),
        None,
    )
    if duplicate or (state.history and music_library.track_key(state.history[-1]) == key):
        location = "đang phát" if state.history and music_library.track_key(state.history[-1]) == key else f"đã ở vị trí #{duplicate}"
        return {"ok": False, "reason": f"Bài này {location}, nên Peto không thêm trùng"}
    state.queue.append({**song, "requester": requester})

    duration = int(song.get("duration") or 0)
    is_radio = song.get("source") == "radio"
    if (
        (vc.is_playing() or vc.is_paused())
        and len(state.queue) == 1
        and not is_radio
        and not song.get("youtube_metadata_failed")
        and duration <= 600
    ):
        asyncio.create_task(preload_audio(song["url"], delay=3.0))

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

    duration = int(song.get("duration") or 0)
    is_radio = song.get("source") == "radio"
    is_too_long = duration > 600
    use_long_temp_file = should_use_long_audio_temp(
        duration,
        is_radio=is_radio,
    )
    use_direct_stream = is_radio or (is_too_long and not use_long_temp_file)
    source = song["url"]
    fallback_info = None
    fallback_attempted = False

    # /play can preserve initial YouTube page metadata even when the extractor
    # returns no playable formats. This is the earliest failure point, so try
    # the external providers before starting another YouTube download cycle.
    if song.get("youtube_metadata_failed"):
        fallback_attempted = True
        fallback_info = await _try_audio_fallback(song)
        if fallback_info:
            source = fallback_info["stream_url"]
            use_direct_stream = True

    # A successful match is remembered briefly in RAM. Replays during that
    # window can skip another doomed YouTube attempt, while the actual expiring
    # provider stream is always resolved afresh when required.
    preferred_source, preferred_locator = _cached_audio_fallback_hint(song)
    if not fallback_info and not fallback_attempted and preferred_locator:
        fallback_attempted = True
        fallback_info = await _try_audio_fallback(
            song,
            preferred_source=preferred_source,
            preferred_locator=preferred_locator,
        )
        if fallback_info:
            source = fallback_info["stream_url"]
            use_direct_stream = True

    # Cache/file-temp paths let yt-dlp download from the canonical page URL
    # directly.  Resolving a signed stream first would duplicate YouTube player
    # requests and that URL would be discarded immediately afterwards.
    def extract_stream():
        query = song.get("search_query") or song["url"]
        if song.get("source") == "spotify" and not query.startswith("ytsearch"):
            query = f"ytsearch1:{query}"

        info = extract_info_with_retry(query, YDL_OPTIONS, download=False)
        if "entries" in info:
            info = next((entry for entry in info["entries"] if entry), None)
        return info

    if not fallback_info and _needs_stream_lookup(
        song,
        use_direct_stream=use_direct_stream,
    ):
        loop = bot.loop
        try:
            logger.info(
                "yt-dlp phase=stream-metadata title=%r guild=%s",
                song.get("title", "Unknown"),
                vc.guild.id,
            )
            info = await loop.run_in_executor(None, extract_stream)
            if not info or not info.get("url"):
                raise ValueError("yt-dlp không trả về stream URL")
            source = info["url"]
            if info.get("webpage_url"):
                song["url"] = info["webpage_url"]
            song.pop("youtube_metadata_failed", None)
        except Exception as error:
            if not fallback_attempted:
                fallback_attempted = True
                fallback_info = await _try_audio_fallback(song, error=error)
            if fallback_info:
                source = fallback_info["stream_url"]
                use_direct_stream = True
            else:
                logger.warning(
                    "yt-dlp phase=stream-metadata thất bại, bỏ qua bài %r "
                    "(guild=%s): %s",
                    song.get("title", "Unknown"),
                    vc.guild.id,
                    error,
                )
                return await _play_next_locked(bot, vc, channel, state)

    # ==============================
    # AFTER PLAYING CALLBACK
    # ==============================
    def after_playing(error):
        if error:
            logger.warning("Audio player báo lỗi (guild=%s): %s", vc.guild.id, error)

        guild_id = vc.guild.id

        is_skip = getattr(vc, 'skip_request', False)
        is_prev = getattr(vc, 'is_previous_action', False)
        is_stop = getattr(vc, 'stop_request', False)

        started = getattr(vc, "play_start_time", time.time())
        paused = getattr(vc, "total_paused_duration", 0) or 0
        if getattr(vc, "paused_at", None):
            paused += max(0, time.time() - vc.paused_at)
        listened = max(0, int(time.time() - started - paused))
        _schedule_from_audio_thread(
            bot,
            _record_play_event_safely(guild_id, song, listened, bool(is_skip or is_prev or is_stop)),
            guild_id=guild_id,
            action="lưu thống kê nghe",
        )

        if hasattr(vc, 'skip_request'): del vc.skip_request
        if hasattr(vc, 'is_previous_action'): del vc.is_previous_action
        if hasattr(vc, 'stop_request'): del vc.stop_request

        if is_stop:
            return

        if not vc or not vc.is_connected():
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
    # ==============================
    # LOADING PANEL V2 (hiện ngay trong lúc tải/cache/normalize nhạc)
    # ==============================
    loading_msg = None
    if not is_radio and not use_direct_stream:
        loading_view = create_loading_panel(song, long_track=use_long_temp_file)

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
        # Radio luôn giữ đường stream cũ. Máy nhà cũng tiếp tục stream nhạc dài.
        # Trên VPS dùng proxy, yt-dlp và FFmpeg có IP ra khác nhau nên signed URL
        # Googlevideo bị 403; tải file tạm qua yt-dlp để cả bài chỉ dùng một IP.
        if use_direct_stream:
            logger.info(
                "Stream trực tiếp %r, duration=%ss (guild=%s)",
                song.get("title"),
                duration,
                vc.guild.id,
            )
            base_source = discord.FFmpegPCMAudio(source, **FFMPEG_OPTIONS)
        elif use_long_temp_file:
            logger.info(
                "Chuẩn bị file tạm cho bài dài %r, duration=%ss (guild=%s)",
                song.get("title"),
                duration,
                vc.guild.id,
            )
            base_source = await get_long_audio_source(song["url"], duration)
        else:
            logger.info(
                "Chuẩn bị cache %r, duration=%ss (guild=%s)",
                song.get("title"),
                duration,
                vc.guild.id,
            )
            base_source = await get_audio_source(song['url'])
            song.pop("youtube_metadata_failed", None)
    except Exception as e:
        # A failing matched provider gets one fresh resolve and may move on to
        # the next enabled provider. A failed search earlier in this playback
        # is not repeated after the final YouTube attempt.
        replacement = None
        if fallback_info:
            replacement = await _try_audio_fallback(
                song,
                preferred_source=song.get("fallback_source"),
                preferred_locator=song.get("fallback_locator"),
            )
        elif not fallback_attempted:
            fallback_attempted = True
            replacement = await _try_audio_fallback(song, error=e)
        if replacement:
            try:
                source = replacement["stream_url"]
                fallback_info = replacement
                use_direct_stream = True
                base_source = discord.FFmpegPCMAudio(source, **FFMPEG_OPTIONS)
            except Exception as fallback_error:
                e = fallback_error
                replacement = None

        if not replacement:
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
    
    # Radio/stream dài vẫn giữ nguyên đường PCM + VolumeTransformer cũ.
    # Cache nhạc ngắn là Opus trực tiếp nên không được bọc PCMVolumeTransformer,
    # tránh decode rồi encode lossy thêm một lần trước khi gửi Discord.
    audio_source = None
    try:
        if use_direct_stream:
            audio_source = discord.PCMVolumeTransformer(base_source)
            audio_source.volume = getattr(vc, 'current_volume', 1.0)
        else:
            audio_source = base_source

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
        if (
            not next_is_radio
            and not next_song.get("stream_only")
            and not next_song.get("youtube_metadata_failed")
            and next_duration <= 600
        ):
            asyncio.create_task(preload_audio(next_song['url'], delay=3.0))
            
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
