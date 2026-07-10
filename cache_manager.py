import os
import json
import hashlib
import asyncio
import subprocess
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


async def get_audio_source(url):
    """Hàm chính để xuất ra FFmpegPCMAudio từ file cache cục bộ (đã normalize)."""
    raw_outtmpl, final_path = get_cache_paths(url)

    # 1. Cache Hit (file đã tồn tại và không bị lỗi 0 byte)
    if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
        print(f"🎵 [CACHE HIT] Đang phát file cục bộ (đã normalize): {final_path}")
        return discord.FFmpegPCMAudio(final_path, options="-vn")

    print(f"⬇️ [CACHE MISS] Tải, đo loudness (pass 1) & normalize (pass 2): {url}")
    # 2. Tải + đo + encode ngầm bằng asyncio.to_thread (không block event loop của bot)
    await asyncio.to_thread(build_cache_sync, url, raw_outtmpl, final_path)

    print(f"✅ Đã cache + normalize xong, bắt đầu phát!")
    return discord.FFmpegPCMAudio(final_path, options="-vn")