import os
import json
import hashlib
import asyncio
import logging
import glob
import subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager

import yt_dlp
import discord
from ytdlp_support import youtube_ydl_options


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


def _download_long_audio_sync(url):
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
    last_error = None
    for attempt in range(1, 3):
        filepath = None
        try:
            logger.info(
                "Tải audio tạm cho bài dài (lần %s/2): %s",
                attempt,
                url,
            )
            async with _long_audio_download_semaphore:
                filepath, codec = await asyncio.to_thread(
                    _download_long_audio_sync,
                    url,
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
                "Không chuẩn bị được audio tạm bài dài (lần %s/2): %s",
                attempt,
                error,
            )
            if attempt < 2:
                await asyncio.sleep(3)

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


def download_raw_sync(url, raw_outtmpl):
    """
    Tải audio tốt nhất từ YouTube, KHÔNG transcode ở bước này (giữ nguyên chất
    lượng gốc, thường là Opus/AAC ~128-160kbps). Trước đây bot dùng
    FFmpegExtractAudio để ép ngay về MP3 128k lúc tải -> bị encode lossy chồng
    lossy 2 lần. Giờ chỉ transcode ĐÚNG MỘT LẦN ở normalize_and_encode_sync().
    """
    ydl_opts = youtube_ydl_options({
        "format": "bestaudio/best",
        "outtmpl": raw_outtmpl,
        "quiet": True,
        "noplaylist": True,
        "nocheckcertificate": True,
    })
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info:
            info = info["entries"][0]
        # yt-dlp có thể chọn extension thật khác với %(ext)s dự kiến ban đầu
        # -> luôn lấy tên file thật qua prepare_filename()
        return ydl.prepare_filename(info)


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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return None

    stderr = result.stderr
    start = stderr.rfind('{')
    if start == -1:
        return None
    try:
        return json.loads(stderr[start:])
    except json.JSONDecodeError:
        return None


def normalize_and_encode_sync(raw_path, final_path, stats):
    """
    PASS 2 (2-pass loudnorm): áp MỘT hệ số gain TĨNH dựa trên số đo thật ở
    pass 1 (linear=true). Đây là điểm khác biệt quan trọng nhất so với trước:
    KHÔNG còn gain "rượt" theo thời gian thực lúc phát -> hết hiện tượng
    pumping (to nhỏ thất thường). Đồng thời mọi bài đều được đưa về cùng
    một mức loudness -16 LUFS -> nghe đều nhau giữa các bài.
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
        loudnorm_filter = f"loudnorm={LOUDNORM_TARGET}"

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
    subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=180)


def build_cache_sync(url, raw_outtmpl, final_path):
    raw_path = download_raw_sync(url, raw_outtmpl)
    try:
        stats = measure_loudness_sync(raw_path)
        normalize_and_encode_sync(raw_path, final_path, stats)
    finally:
        # File raw chỉ là trung gian để đo/encode, xoá đi cho đỡ tốn ổ đĩa
        try:
            os.remove(raw_path)
        except OSError:
            pass


async def ensure_audio_cached(url):
    """Trả về file Opus cache, tránh tải/encode trùng cùng một URL."""
    raw_outtmpl, final_path = get_cache_paths(url)

    if _is_valid_file(final_path):
        return final_path

    lock = _get_lock(_cache_locks, url)
    async with lock:
        if not _is_valid_file(final_path):
            # Loudness pass + encode Opus khá nặng CPU. Chỉ cho một cache build
            # chạy tại một thời điểm để không giành tài nguyên với audio thread.
            async with _cache_build_semaphore:
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


async def get_audio_source(url):
    """Trả về nguồn Opus cache có preroll, không decode/encode lại."""
    _, final_path = get_cache_paths(url)

    if _is_valid_file(final_path):
        logger.info("Cache hit: %s", final_path)
        opus_source = discord.FFmpegOpusAudio(final_path, codec="copy", options="-vn")
        return OpusPrerollAudioSource(opus_source)

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
    _, final_path = get_cache_paths(url)

    # Nếu đã có sẵn trong ổ cứng thì bỏ qua luôn
    if _is_valid_file(final_path):
        return

    if delay > 0:
        await asyncio.sleep(delay)
        # Bài có thể đã được cache bởi tác vụ khác trong thời gian chờ.
        if _is_valid_file(final_path):
            return

    logger.info("Bắt đầu tải trước: %s", url)
    try:
        await ensure_audio_cached(url)
        logger.info("Tải trước thành công: %s", url)
    except Exception as e:
        logger.warning("Không thể tải trước %s: %s", url, e)
