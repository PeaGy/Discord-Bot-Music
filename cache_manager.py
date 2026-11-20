import os
import hashlib
import asyncio
import logging
import subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager

import yt_dlp
import discord
from discord.oggparse import OggError, OggStream
from music.audio_source import OPUS_PREROLL_FRAMES, OPUS_SILENCE_PACKET, OpusPrerollAudioSource
from music.download import (
    AUDIO_CACHE_DIR as CACHE_DIR,
    AudioDownloadError,
    can_continue_download_attempt as _can_continue_download_attempt,
    is_valid_file as _is_valid_file,
    remove_download_bundle,
    youtube_download_attempt_specs as _youtube_download_attempt_specs,
)
from music.growing_opus import GrowingOpusFile
from music.long_audio import (
    LONG_AUDIO_TEMP_DIR,
    LONG_AUDIO_TEMP_MAX_BYTES,
    LONG_AUDIO_TEMP_MAX_DURATION,
    LONG_AUDIO_TEMP_STALE_SECONDS,
    TemporaryFileAudioSource,
    cleanup_stale_long_audio_files,
    get_long_audio_source,
)
from music.loudness import (
    LOUDNORM_TARGET,
    OPUS_EXPECTED_PACKET_LOSS,
    fast_measurement_command,
    measurement_command,
    normalization_command,
    parse_linear_measurement,
    parse_loudness_stats as _parse_loudness_stats,
)
from music.urls import canonical_youtube_url
from music.timing import phase_timer, log_phase_timing
from ytdlp_support import (
    ydl_options_for_player_client,
    youtube_ydl_options,
    take_youtube_download_seed,
)


logger = logging.getLogger(__name__)

DOWNLOAD_MP3_BITRATE = "128k"
CACHE_FORMAT_VERSION = "v2_fec10"
_cache_locks = {}
_playback_builds = {}
_download_locks = {}
_cache_build_semaphore = asyncio.Semaphore(1)


class CachedOpusSource(discord.AudioSource):
    """Read Peto's local 20 ms Ogg Opus packets directly on the voice thread."""

    def __init__(self, stream, packets, first):
        self.stream = stream
        self.packets = packets
        self.first = first

    def read(self):
        if self.first is not None:
            packet, self.first = self.first, None
            return packet
        return next(self.packets, b"")

    def is_opus(self):
        return True

    def cleanup(self):
        self.stream.close()


def _read_cached_opus(stream):
    try:
        packets = OggStream(stream).iter_packets()
        head = next(packets, b"")
        # Only the ordinary mono/stereo OpusHead written by our cache encoder.
        if (len(head) == 19 and head[:9] == b"OpusHead\x01" and head[9] in (1, 2)
                and head[16:19] == b"\x00\x00\x00" and next(packets, b"").startswith(b"OpusTags")):
            first = next(packets, b"")
            if first:
                return CachedOpusSource(stream, packets, first)
    except OggError:
        pass
    except BaseException:
        stream.close()
        raise
    stream.close()
    return None


def _open_cached_opus(path):
    cached = _read_cached_opus(open(path, "rb"))
    if cached is not None:
        return cached
    return OpusPrerollAudioSource(discord.FFmpegOpusAudio(path, codec="copy", options="-vn"))


def is_cache_build_active():
    """Cho lệnh chẩn đoán biết FFmpeg có đang tạo cache hay không."""
    return _cache_build_semaphore.locked()


def _get_lock(lock_store, url):
    lock_key = hashlib.md5(url.encode("utf-8")).hexdigest()
    lock = lock_store.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        lock_store[lock_key] = lock
    return lock


def get_cache_paths(url):
    """
    Trả về (raw_outtmpl, final_path).
    raw_outtmpl: nơi tải file gốc tạm thời (chưa qua xử lý) về, sẽ bị xoá sau khi xong.
    final_path: file cache thật sự được phát nhạc từ đó (đã normalize loudness).
    """
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
    raw_outtmpl = os.path.join(CACHE_DIR, f"raw_{url_hash}.%(ext)s")
    final_path = os.path.join(
        CACHE_DIR,
        f"track_{CACHE_FORMAT_VERSION}_{url_hash}.opus",
    )
    return raw_outtmpl, final_path


def find_cached_audio_path(url):
    """Cache-only lookup; preserve legacy hashes and accept YouTube URL aliases."""
    urls = [str(url)]
    canonical = canonical_youtube_url(url)
    if canonical and canonical not in urls:
        urls.append(canonical)
    for candidate in urls:
        _, path = get_cache_paths(candidate)
        try:
            if os.path.isfile(path) and _is_valid_file(path):
                return path
        except OSError:
            continue
    return None


def _cache_timing_id(url):
    """Correlate phases without logging URLs, query strings or credentials."""
    canonical = canonical_youtube_url(url)
    return canonical.split("=", 1)[1] if canonical else hashlib.md5(url.encode("utf-8")).hexdigest()[:12]


def _download_resolved_audio(ydl, info, track_id):
    if info and "entries" in info:
        info = next((entry for entry in info["entries"] if entry), None)
    if not info:
        raise AudioDownloadError("yt-dlp không trả về thông tin audio.")
    # This is yt-dlp's normal download-from-info path. Format selection and
    # downloader/fixup handling stay with yt-dlp, on the same network session.
    with phase_timer(logger, "audio-transfer", track_id=track_id):
        downloaded = ydl.process_ie_result(info, download=True)
    if not downloaded:
        raise AudioDownloadError("yt-dlp không trả về kết quả tải audio.")
    return ydl.prepare_filename(downloaded)


def download_raw_sync(url, raw_outtmpl):
    """
    Tải audio tốt nhất từ YouTube, KHÔNG transcode ở bước này (giữ nguyên chất
    lượng gốc, thường là Opus/AAC ~128-160kbps). Trước đây bot dùng
    FFmpegExtractAudio để ép ngay về MP3 128k lúc tải -> bị encode lossy chồng
    lossy 2 lần. Giờ chỉ transcode ĐÚNG MỘT LẦN ở normalize_and_encode_sync().
    """
    base_options = youtube_ydl_options({
        "format": "bestaudio/best",
        "outtmpl": raw_outtmpl,
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "continuedl": True,
        "retries": 2,
        "fragment_retries": 2,
    })
    attempt_specs = _youtube_download_attempt_specs(base_options, url)
    track_id = _cache_timing_id(url)

    # Consume at most one fresh handoff. It never goes into a song/DB, and a
    # failed or expired signed URL falls back to the bounded extraction loop.
    seed = take_youtube_download_seed(url, base_options)
    if seed is not None:
        try:
            logger.info(
                "yt-dlp phase=short-download reuse=fresh-metadata "
                "route=%s client=%s track=%s",
                seed.route_name, seed.player_client or "auto", track_id,
            )
            with yt_dlp.YoutubeDL(seed.options) as ydl:
                if seed.cookiejar is not None:
                    ydl.cookiejar = seed.cookiejar
                return _download_resolved_audio(ydl, seed.info, track_id)
        except Exception as error:
            remove_download_bundle(raw_outtmpl.split("%", 1)[0])
            logger.info(
                "yt-dlp phase=download-seed-refresh track=%s reason=%s",
                track_id, type(error).__name__,
            )

    last_error = None
    total_attempts = len(attempt_specs)
    for attempt, spec in enumerate(attempt_specs, start=1):
        route_name, route_options, client = spec
        options = ydl_options_for_player_client(route_options, client)
        try:
            logger.info(
                "yt-dlp phase=short-download "
                "attempt=%s/%s route=%s client=%s url=%s",
                attempt,
                total_attempts,
                route_name,
                client or "auto",
                url,
            )
            with yt_dlp.YoutubeDL(options) as ydl:
                with phase_timer(logger, "download-metadata", track_id=track_id):
                    info = ydl.extract_info(url, download=False)
                return _download_resolved_audio(ydl, info, track_id)
        except Exception as error:
            last_error = error
            prefix = raw_outtmpl.split("%", 1)[0]
            remove_download_bundle(prefix)
            if attempt >= total_attempts:
                raise
            next_spec = attempt_specs[attempt]
            next_route, _, next_client = next_spec
            if not _can_continue_download_attempt(error, spec, next_spec):
                raise
            logger.warning(
                "yt-dlp phase=short-download lỗi "
                "(lần %s/%s, route=%s, client=%s): %s "
                "— thử lại route=%s client=%s sau 3s",
                attempt,
                total_attempts,
                route_name,
                client or "auto",
                error,
                next_route,
                next_client or "auto",
            )
            time.sleep(3)

    raise AudioDownloadError("Không tải được audio để tạo cache.") from last_error


def measure_loudness_sync(filepath):
    """
    Đo nhanh để chọn gain cố định; khi cần, đo bằng loudnorm để lấy offset.
    Cả hai lượt đo dùng chung ngân sách thời gian khi tạo cache.
    """
    try:
        deadline = time.monotonic() + 120
        for command, parse in ((fast_measurement_command, parse_linear_measurement),
                               (measurement_command, _parse_loudness_stats)):
            result = subprocess.run(
                command(filepath), capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=max(0, deadline - time.monotonic()),
            )
            if result.returncode == 0:
                stats = parse(result.stderr)
                if stats is not None:
                    return stats
    except subprocess.TimeoutExpired:
        logger.warning("audio-normalization measure-error reason=timeout")
        return None
    except OSError:
        logger.warning("audio-normalization measure-error reason=process-start")
        return None
    if result.returncode != 0:
        logger.warning("audio-normalization measure-error reason=ffmpeg-exit")
        return None
    logger.warning("audio-normalization measure-error reason=invalid-or-missing-stats")
    return None


def normalize_and_encode_sync(raw_path, final_path, stats, *, progressive=False):
    """
    PASS 2: ưu tiên gain tuyến tính dựa trên số đo pass 1 (linear=true).
    FFmpeg có thể chuyển sang dynamic nếu giới hạn true peak/LRA không cho
    phép linear. Giữ nguyên mục tiêu -16 LUFS và chất lượng encode của cache;
    mỗi gói được normalize và mã hóa một lần trước khi gửi sang Discord.
    """
    cmd = normalization_command(raw_path, final_path, stats)
    if progressive:
        cmd[-1:-1] = ["-flush_packets", "1"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=180)
    if stats and "gain_db" in stats:
        logger.info("audio-normalization mode=linear")
        return
    # The FFmpeg summary is captured already; report only the known mode, not
    # arbitrary stderr (which can contain paths/URLs or credentials).
    for line in (result.stderr or "").splitlines():
        if line.strip().startswith("Normalization Type:"):
            mode = line.split(":", 1)[1].strip().lower()
            if mode in {"linear", "dynamic"}:
                logger.info("audio-normalization mode=%s", mode)
            break


def build_cache_sync(url, raw_outtmpl, final_path, playback=None):
    track_id = _cache_timing_id(url)
    started = time.perf_counter()
    with phase_timer(logger, "download-total", track_id=track_id):
        raw_path = download_raw_sync(url, raw_outtmpl)
    staged_path = playback.staged_path if playback else f"{final_path}.{uuid.uuid4().hex}.tmp.opus"
    try:
        measured_at = time.perf_counter()
        measure_outcome = "error"
        try:
            stats = measure_loudness_sync(raw_path)
            measure_outcome = "success" if stats else "fallback"
        finally:
            log_phase_timing(logger, "loudness-measure", measured_at, track_id=track_id, outcome=measure_outcome)
        if stats:
            logger.info("audio-normalization measurement=ok strategy=two-pass track=%s", track_id)
        else:
            logger.warning("audio-normalization measurement=unavailable strategy=single-pass track=%s", track_id)
        with phase_timer(logger, "normalize-encode", track_id=track_id):
            if playback:
                normalize_and_encode_sync(raw_path, staged_path, stats, progressive=True)
            else:
                normalize_and_encode_sync(raw_path, staged_path, stats)
        # Publish only a complete file. A concurrent cache-first /play must not
        # mistake the non-empty output of an unfinished encoder for a cache hit.
        with phase_timer(logger, "cache-publish", track_id=track_id):
            if playback:
                playback.publish()
            else:
                os.replace(staged_path, final_path)
        log_phase_timing(logger, "cache-build-total", started, track_id=track_id)
    finally:
        if playback:
            playback.finish()  # Wake a reader even if the encoder or rename failed.
        for temporary_path in (raw_path, staged_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass


async def _ensure_audio_cached(url, playback):
    """Trả về file Opus cache, tránh tải/encode trùng cùng một URL."""
    cached_path = await asyncio.to_thread(find_cached_audio_path, url)
    if cached_path:
        return cached_path
    raw_outtmpl, final_path = get_cache_paths(url)

    if _is_valid_file(final_path):
        return final_path

    lock = _get_lock(_cache_locks, url)
    track_id = _cache_timing_id(url)
    lock_started = time.perf_counter()
    async with lock:
        log_phase_timing(logger, "cache-track-wait", lock_started, track_id=track_id)
        if not _is_valid_file(final_path):
            # Loudness pass + encode Opus khá nặng CPU. Chỉ cho một cache build
            # chạy tại một thời điểm để không giành tài nguyên với audio thread.
            slot_started = time.perf_counter()
            async with _cache_build_semaphore:
                log_phase_timing(logger, "cache-slot-wait", slot_started, track_id=track_id)
                # URL khác có thể đã tạo xong file trong lúc ta chờ semaphore.
                if not _is_valid_file(final_path):
                    await asyncio.to_thread(build_cache_sync, url, raw_outtmpl, final_path, playback)

        if not _is_valid_file(final_path):
            raise AudioDownloadError("Không thể tạo file cache cho bài hát này.")

    return final_path


def _create_download_mp3_sync(source_path):
    """Chuyển file Opus cache sang một MP3 tạm thời."""
    file_descriptor, mp3_path = tempfile.mkstemp(
        prefix="download_",
        suffix=".mp3",
        dir=CACHE_DIR,
    )
    os.close(file_descriptor)

    cmd = [
        "ffmpeg", "-y", "-i", source_path,
        "-vn", "-c:a", "libmp3lame", "-b:a", DOWNLOAD_MP3_BITRATE,
        mp3_path,
    ]

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        if not _is_valid_file(mp3_path):
            raise AudioDownloadError("FFmpeg không tạo được file MP3 hợp lệ.")
        return mp3_path
    except (
        AudioDownloadError,
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        try:
            os.remove(mp3_path)
        except OSError:
            pass
        raise AudioDownloadError("Không thể chuyển bài hát sang MP3.") from error


@asynccontextmanager
async def temporary_download_mp3(url):
    """Tạo MP3 tạm, khóa theo URL và tự xóa sau khi gửi xong."""
    source_path = await ensure_audio_cached(url)
    lock = _get_lock(_download_locks, url)

    async with lock:
        mp3_path = None
        try:
            mp3_path = await asyncio.to_thread(
                _create_download_mp3_sync,
                source_path,
            )
            yield mp3_path
        finally:
            if mp3_path:
                try:
                    os.remove(mp3_path)
                except OSError:
                    pass


async def get_cached_audio_source(url):
    """Return a local Opus source or None, without extraction/download/fallback."""
    final_path = await asyncio.to_thread(find_cached_audio_path, url)
    if not final_path:
        return None
    logger.info("Cache hit: %s", final_path)
    return await asyncio.to_thread(_open_cached_opus, final_path)


def _start_cache_build(url):
    key = canonical_youtube_url(url) or url
    if key in _playback_builds:
        return _playback_builds[key]
    playback = GrowingOpusFile(get_cache_paths(key)[1])
    task = asyncio.create_task(_ensure_audio_cached(key, playback))
    _playback_builds[key] = task, playback

    def done(completed):
        _playback_builds.pop(key, None)
        if not completed.cancelled() and (error := completed.exception()) is not None:
            logger.warning("audio-cache build failed: %s", type(error).__name__)

    task.add_done_callback(done)
    return task, playback


async def ensure_audio_cached(url):
    cached = await asyncio.to_thread(find_cached_audio_path, url)
    if cached:
        return cached
    task, _ = _start_cache_build(url)
    return await asyncio.shield(task)


async def get_audio_source(url):
    """Đọc Opus có sẵn hoặc bắt đầu phát trong lúc tạo cache."""
    cached_source = await get_cached_audio_source(url)
    if cached_source is not None:
        return cached_source

    logger.info("Cache miss, bắt đầu tải và normalize: %s", url)
    started = time.perf_counter()
    # Skips/disconnects release the reader. The producer still owns its lock and
    # finishes the same atomic cache for replays and concurrent download callers.
    build, playback = _start_cache_build(url)
    while not build.done():
        await asyncio.wait((build,), timeout=0.1)
        if not build.done() and await asyncio.to_thread(playback.ready):
            source = await asyncio.to_thread(_read_cached_opus, playback.open_reader())
            if source is not None:
                log_phase_timing(logger, "progressive-opus-ready", started, track_id=_cache_timing_id(url))
                return source
            break
    final_path = await asyncio.shield(build)
    return await asyncio.to_thread(_open_cached_opus, final_path)


async def preload_audio(url, delay=0):
    """
    Hàm tải trước nhạc vào nền (Background Task).
    Chỉ kiểm tra và tải, không trả về Audio Source để tránh block bot.
    """
    # Nếu đã có sẵn trong ổ cứng thì bỏ qua luôn
    if await asyncio.to_thread(find_cached_audio_path, url):
        return

    if delay > 0:
        await asyncio.sleep(delay)
        # Bài có thể đã được cache bởi tác vụ khác trong thời gian chờ.
        if await asyncio.to_thread(find_cached_audio_path, url):
            return

    logger.info("Bắt đầu tải trước: %s", url)
    try:
        await ensure_audio_cached(url)
        logger.info("Tải trước thành công: %s", url)
    except Exception as e:
        logger.warning("Không thể tải trước %s: %s", url, e)
