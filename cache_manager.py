import os
import json
import hashlib
import asyncio
import subprocess
import tempfile
from contextlib import asynccontextmanager

import yt_dlp
import discord

# ==============================
# CACHE DIR
# ==============================
CACHE_DIR = "audio_cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# Target loudness dùng chung cho toàn bộ bot (khớp với radio path bên player.py)
LOUDNORM_TARGET = "I=-16:TP=-1.5:LRA=11"
DOWNLOAD_MP3_BITRATE = "128k"

_cache_locks = {}
_download_locks = {}


class AudioDownloadError(RuntimeError):
    """Lỗi chuẩn bị file âm thanh để gửi cho người dùng."""


def _get_lock(lock_store, url):
    lock_key = hashlib.md5(url.encode("utf-8")).hexdigest()
    lock = lock_store.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        lock_store[lock_key] = lock
    return lock


def _is_valid_file(path):
    return os.path.exists(path) and os.path.getsize(path) > 0


def get_cache_paths(url):
    """
    Trả về (raw_outtmpl, final_path).
    raw_outtmpl: nơi tải file gốc tạm thời (chưa qua xử lý) về, sẽ bị xoá sau khi xong.
    final_path: file cache thật sự được phát nhạc từ đó (đã normalize loudness).
    """
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
    raw_outtmpl = os.path.join(CACHE_DIR, f"raw_{url_hash}.%(ext)s")
    final_path = os.path.join(CACHE_DIR, f"track_{url_hash}.opus")
    return raw_outtmpl, final_path


def download_raw_sync(url, raw_outtmpl):
    """
    Tải audio tốt nhất từ YouTube, KHÔNG transcode ở bước này (giữ nguyên chất
    lượng gốc, thường là Opus/AAC ~128-160kbps). Trước đây bot dùng
    FFmpegExtractAudio để ép ngay về MP3 128k lúc tải -> bị encode lossy chồng
    lossy 2 lần. Giờ chỉ transcode ĐÚNG MỘT LẦN ở normalize_and_encode_sync().
    """
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": raw_outtmpl,
        "quiet": True,
        "noplaylist": True,
        "nocheckcertificate": True,
    }
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
    """Hàm chính để xuất ra FFmpegPCMAudio từ file cache cục bộ (đã normalize)."""
    _, final_path = get_cache_paths(url)

    if _is_valid_file(final_path):
        print(f"🎵 [CACHE HIT] Đang phát file cục bộ (đã normalize): {final_path}")
        return discord.FFmpegPCMAudio(final_path, options="-vn")

    print(f"⬇️ [CACHE MISS] Tải, đo loudness (pass 1) & normalize (pass 2): {url}")
    final_path = await ensure_audio_cached(url)

    print(f"✅ Đã cache + normalize xong, bắt đầu phát!")
    return discord.FFmpegPCMAudio(final_path, options="-vn")


async def preload_audio(url):
    """
    Hàm tải trước nhạc vào nền (Background Task).
    Chỉ kiểm tra và tải, không trả về Audio Source để tránh block bot.
    """
    _, final_path = get_cache_paths(url)

    # Nếu đã có sẵn trong ổ cứng thì bỏ qua luôn
    if _is_valid_file(final_path):
        return 

    print(f"🔄 [PRELOAD] Đang âm thầm tải trước bài hát vào Cache...")
    try:
        await ensure_audio_cached(url)
        print(f"✅ [PRELOAD] Tải trước thành công! Bài tiếp theo đã sẵn sàng.")
    except Exception as e:
        print(f"❌ [PRELOAD LỖI]: Không thể tải trước - {e}")
