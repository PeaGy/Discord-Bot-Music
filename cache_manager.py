import os
import json
import hashlib
import asyncio
import logging
import math
import re
import glob
import subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager

import yt_dlp
import discord
from music.urls import canonical_youtube_url
from music.timing import phase_timer, log_phase_timing
from ytdlp_support import (
    is_transient_ytdlp_error,
    ydl_options_for_player_client,
    youtube_player_clients,
    youtube_ydl_options,
    youtube_ydl_routes,
    take_youtube_download_seed,
)


logger = logging.getLogger(__name__)

# ==============================
# CACHE DIR
# ==============================
CACHE_DIR = "audio_cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

LONG_AUDIO_TEMP_DIR = os.path.join(CACHE_DIR, "long_temp")
os.makedirs(LONG_AUDIO_TEMP_DIR, exist_ok=True)

# Target loudness dùng chung cho toàn bộ bot (khớp với radio path bên player.py)
LOUDNORM_TARGET = "I=-16:TP=-1.5:LRA=11"
DOWNLOAD_MP3_BITRATE = "128k"
CACHE_FORMAT_VERSION = "v2_fec10"
OPUS_EXPECTED_PACKET_LOSS = 10
OPUS_PREROLL_FRAMES = 8  # 8 x 20 ms = 160 ms để ổn định nhịp gửi lúc bắt đầu
OPUS_SILENCE_PACKET = b"\xF8\xFF\xFE"
LONG_AUDIO_TEMP_MAX_DURATION = 2 * 60 * 60
LONG_AUDIO_TEMP_MAX_BYTES = 300 * 1024 * 1024
LONG_AUDIO_TEMP_STALE_SECONDS = 6 * 60 * 60

_cache_locks = {}
_download_locks = {}
_cache_build_semaphore = asyncio.Semaphore(1)
_long_audio_download_semaphore = asyncio.Semaphore(2)


class AudioDownloadError(RuntimeError):
    """Lỗi chuẩn bị file âm thanh để gửi cho người dùng."""


class OpusPrerollAudioSource(discord.AudioSource):
    """Phát vài frame Opus im lặng trước khi đọc file thật.

    FFmpeg được khởi động ngay khi tạo ``source`` nên khoảng đệm này cho tiến trình
    đủ thời gian nạp packet đầu tiên, tránh làm audio thread trễ nhịp 20 ms của
    Discord. Nguồn vẫn là Opus xuyên suốt và không bị encode lại.
    """

    def __init__(self, source, frames=OPUS_PREROLL_FRAMES):
        if not source.is_opus():
            raise TypeError("OpusPrerollAudioSource yêu cầu nguồn Opus.")
        self.source = source
        self.remaining_frames = max(0, int(frames))

    def read(self):
        if self.remaining_frames > 0:
            self.remaining_frames -= 1
            return OPUS_SILENCE_PACKET
        return self.source.read()

    def is_opus(self):
        return True

    def cleanup(self):
        self.source.cleanup()


class TemporaryFileAudioSource(discord.AudioSource):
    """Audio source that removes its downloaded file when Discord is done with it."""

    def __init__(self, source, filepath):
        self.source = source
        self.filepath = filepath
        self._cleaned = False

    def read(self):
        return self.source.read()

    def is_opus(self):
        return self.source.is_opus()

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        try:
            self.source.cleanup()
        finally:
            try:
                os.remove(self.filepath)
                logger.info("Đã xóa audio tạm của bài dài: %s", self.filepath)
            except FileNotFoundError:
                pass
            except OSError as error:
                logger.warning("Không xóa được audio tạm %s: %s", self.filepath, error)


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


def _is_valid_file(path):
    return os.path.exists(path) and os.path.getsize(path) > 0


def cleanup_stale_long_audio_files(max_age=LONG_AUDIO_TEMP_STALE_SECONDS):
    """Remove abandoned long-track files left behind by a crash or forced shutdown."""
    cutoff = time.time() - max(0, int(max_age))
    try:
        entries = os.scandir(LONG_AUDIO_TEMP_DIR)
    except OSError as error:
        logger.warning("Không quét được thư mục audio tạm: %s", error)
        return

    with entries:
        for entry in entries:
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
            except OSError:
                continue


def _remove_long_audio_bundle(prefix):
    for path in glob.glob(f"{glob.escape(prefix)}*"):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def _youtube_download_attempt_specs(base_options, url):
    """Return client attempts for WARP first, then direct VPS egress."""
    clients = youtube_player_clients()
    if clients:
        attempt_clients = clients[:2]
        if len(attempt_clients) == 1:
            attempt_clients = (attempt_clients[0], attempt_clients[0])
    else:
        # Preserve the old home-PC behavior: one automatic-client attempt.
        attempt_clients = (None,)

    return tuple(
        (route_name, route_options, client)
        for route_name, route_options in youtube_ydl_routes(
            base_options,
            query=url,
        )
        for client in attempt_clients
    )


def _can_continue_download_attempt(error, current_spec, next_spec):
    current_route, _, current_client = current_spec
    next_route, _, next_client = next_spec
    if next_route != current_route:
        return is_transient_ytdlp_error(error)
    return next_client != current_client or is_transient_ytdlp_error(error)


def _download_long_audio_sync(url, player_client=None, route_name=None):
    """Download original long-form audio through yt-dlp without normalization."""
    token = uuid.uuid4().hex
    prefix = os.path.join(LONG_AUDIO_TEMP_DIR, f"long_{token}_")
    outtmpl = f"{prefix}%(id)s.%(ext)s"
    ydl_opts = youtube_ydl_options({
        "format": "bestaudio[acodec^=opus]/bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "continuedl": True,
        "retries": 2,
        "fragment_retries": 2,
        "max_filesize": LONG_AUDIO_TEMP_MAX_BYTES,
    })
    available_routes = dict(youtube_ydl_routes(ydl_opts, query=url))
    if route_name in available_routes:
        ydl_opts = available_routes[route_name]
    ydl_opts = ydl_options_for_player_client(ydl_opts, player_client)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if "entries" in info:
                info = next((entry for entry in info["entries"] if entry), None)
            if not info:
                raise AudioDownloadError("yt-dlp không trả về thông tin audio.")

            filepath = ydl.prepare_filename(info)
            if not _is_valid_file(filepath):
                candidates = [
                    path for path in glob.glob(f"{glob.escape(prefix)}*")
                    if _is_valid_file(path) and not path.endswith(".part")
                ]
                if not candidates:
                    raise AudioDownloadError("Không tìm thấy file audio dài sau khi tải.")
                filepath = max(candidates, key=os.path.getmtime)

            if os.path.getsize(filepath) > LONG_AUDIO_TEMP_MAX_BYTES:
                raise AudioDownloadError("Audio dài vượt quá giới hạn 300 MiB.")

            requested = info.get("requested_downloads") or []
            selected = requested[0] if requested else info
            codec = str(selected.get("acodec") or info.get("acodec") or "").lower()
            return filepath, codec
    except Exception:
        _remove_long_audio_bundle(prefix)
        raise


async def get_long_audio_source(url, duration):
    """Prepare a temporary local source for a proxied long track, with one retry."""
    duration = int(duration or 0)
    if duration > LONG_AUDIO_TEMP_MAX_DURATION:
        raise AudioDownloadError(
            "Bài dài vượt quá giới hạn phát tạm 2 giờ trên VPS."
        )

    cleanup_stale_long_audio_files()
    base_options = youtube_ydl_options({})
    attempt_specs = _youtube_download_attempt_specs(base_options, url)
    last_error = None
    total_attempts = len(attempt_specs)
    for attempt, spec in enumerate(attempt_specs, start=1):
        route_name, _, player_client = spec
        filepath = None
        try:
            logger.info(
                "Tải audio tạm cho bài dài "
                "(lần %s/%s, route=%s, client=%s): %s",
                attempt,
                total_attempts,
                route_name,
                player_client or "auto",
                url,
            )
            async with _long_audio_download_semaphore:
                filepath, codec = await asyncio.to_thread(
                    _download_long_audio_sync,
                    url,
                    player_client,
                    route_name,
                )
            ffmpeg_source = discord.FFmpegOpusAudio(
                filepath,
                codec="copy" if "opus" in codec else "libopus",
                bitrate=160,
                options="-vn",
            )
            logger.info(
                "Audio tạm bài dài sẵn sàng: %s (%s)",
                filepath,
                codec or "codec không rõ",
            )
            return TemporaryFileAudioSource(
                OpusPrerollAudioSource(ffmpeg_source),
                filepath,
            )
        except Exception as error:
            last_error = error
            if filepath:
                try:
                    os.remove(filepath)
                except OSError:
                    pass
            logger.warning(
                "Không chuẩn bị được audio tạm bài dài "
                "(lần %s/%s, route=%s, client=%s): %s",
                attempt,
                total_attempts,
                route_name,
                player_client or "auto",
                error,
            )
            if attempt < total_attempts and _can_continue_download_attempt(
                error,
                spec,
                attempt_specs[attempt],
            ):
                await asyncio.sleep(3)
                continue
            break

    raise AudioDownloadError("Không tải được audio tạm cho bài dài.") from last_error


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
            _remove_long_audio_bundle(raw_outtmpl.split("%", 1)[0])
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
            _remove_long_audio_bundle(prefix)
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


_LOUDNESS_FIELDS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
_LOUDNESS_FIELD_MARKER = re.compile(r'"(?:' + "|".join(_LOUDNESS_FIELDS) + r')"\s*:')


def _parse_loudness_stats(stderr):
    """Read one complete loudnorm report, independent of surrounding FFmpeg logs.

    raw_decode stops at the closing JSON brace; json.loads on the whole stderr
    tail rejects valid reports followed by muxer/progress messages. Unrelated
    JSON is ignored, but ambiguous or broken loudnorm reports aren't reused.
    """
    if not isinstance(stderr, str):
        return None
    decoder = json.JSONDecoder()
    stats = None
    position = 0
    while True:
        start = stderr.find("{", position)
        if start < 0:
            return stats
        try:
            value, end = decoder.raw_decode(stderr, start)
        except (json.JSONDecodeError, RecursionError):
            # Limit the lookahead to this apparent object. A stray brace in an
            # earlier log line must not swallow the genuine report after it.
            next_start = stderr.find("{", start + 1)
            fragment = stderr[start:next_start if next_start >= 0 else len(stderr)]
            if _LOUDNESS_FIELD_MARKER.search(fragment):
                return None
            position = start + 1
            continue
        position = end
        if not isinstance(value, dict) or not any(key in value for key in _LOUDNESS_FIELDS):
            continue
        if stats is not None or not all(key in value for key in _LOUDNESS_FIELDS):
            return None
        parsed = {}
        for key in _LOUDNESS_FIELDS:
            number = value[key]
            if isinstance(number, bool) or not isinstance(number, (str, int, float)):
                return None
            try:
                number = float(number)
            except (ValueError, TypeError, OverflowError):
                return None
            if not math.isfinite(number):
                # Silence can produce -inf. It has no usable two-pass gain;
                # keep the existing single-pass fallback instead of failing.
                return None
            parsed[key] = format(number, ".12g")
        stats = parsed


def measure_loudness_sync(filepath):
    """
    PASS 1 (2-pass loudnorm): chỉ ĐO loudness thật của file, không sửa gì.
    Chạy 1 lần duy nhất lúc cache bài hát -> không tốn CPU lúc đang phát nhạc.
    """
    cmd = [
        "ffmpeg", "-i", filepath,
        "-af", f"loudnorm={LOUDNORM_TARGET}:print_format=json",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except subprocess.TimeoutExpired:
        logger.warning("audio-normalization measure-error reason=timeout")
        return None
    except OSError:
        logger.warning("audio-normalization measure-error reason=process-start")
        return None
    if result.returncode != 0:
        logger.warning("audio-normalization measure-error reason=ffmpeg-exit")
        return None
    stats = _parse_loudness_stats(result.stderr)
    if stats is None:
        logger.warning("audio-normalization measure-error reason=invalid-or-missing-stats")
    return stats


def normalize_and_encode_sync(raw_path, final_path, stats):
    """
    PASS 2: ưu tiên gain tuyến tính dựa trên số đo pass 1 (linear=true).
    FFmpeg có thể chuyển sang dynamic nếu giới hạn true peak/LRA không cho
    phép linear. Giữ nguyên mục tiêu -16 LUFS và chất lượng encode của cache;
    toàn bộ xử lý hoàn tất trước khi phát, không normalize lại lúc phát.
    """
    if stats:
        loudnorm_filter = (
            f"loudnorm={LOUDNORM_TARGET}:"
            f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true:print_format=summary"
        )
    else:
        # Không đo được (file lỗi / ffmpeg cũ) -> fallback single-pass, vẫn còn
        # hơn là không normalize gì cả
        loudnorm_filter = f"loudnorm={LOUDNORM_TARGET}:print_format=summary"

    cmd = [
        "ffmpeg", "-y", "-i", raw_path,
        "-vn", "-af", loudnorm_filter,
        "-c:a", "libopus", "-b:a", "160k",
        "-application", "audio",
        "-frame_duration", "20",
        "-packet_loss", str(OPUS_EXPECTED_PACKET_LOSS),
        "-fec", "1",
        final_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=180)
    # The FFmpeg summary is captured already; report only the known mode, not
    # arbitrary stderr (which can contain paths/URLs or credentials).
    for line in (result.stderr or "").splitlines():
        if line.strip().startswith("Normalization Type:"):
            mode = line.split(":", 1)[1].strip().lower()
            if mode in {"linear", "dynamic"}:
                logger.info("audio-normalization mode=%s", mode)
            break


def build_cache_sync(url, raw_outtmpl, final_path):
    track_id = _cache_timing_id(url)
    started = time.perf_counter()
    with phase_timer(logger, "download-total", track_id=track_id):
        raw_path = download_raw_sync(url, raw_outtmpl)
    staged_path = f"{final_path}.{uuid.uuid4().hex}.tmp.opus"
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
            normalize_and_encode_sync(raw_path, staged_path, stats)
        # Publish only a complete file. A concurrent cache-first /play must not
        # mistake the non-empty output of an unfinished encoder for a cache hit.
        with phase_timer(logger, "cache-publish", track_id=track_id):
            os.replace(staged_path, final_path)
        log_phase_timing(logger, "cache-build-total", started, track_id=track_id)
    finally:
        for temporary_path in (raw_path, staged_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass


async def ensure_audio_cached(url):
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
                    await asyncio.to_thread(build_cache_sync, url, raw_outtmpl, final_path)

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
    opus_source = discord.FFmpegOpusAudio(final_path, codec="copy", options="-vn")
    return OpusPrerollAudioSource(opus_source)


async def get_audio_source(url):
    """Trả về nguồn Opus cache có preroll, không decode/encode lại."""
    cached_source = await get_cached_audio_source(url)
    if cached_source is not None:
        return cached_source

    logger.info("Cache miss, bắt đầu tải và normalize: %s", url)
    final_path = await ensure_audio_cached(url)

    logger.info("Đã cache và normalize: %s", final_path)
    opus_source = discord.FFmpegOpusAudio(final_path, codec="copy", options="-vn")
    return OpusPrerollAudioSource(opus_source)


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
