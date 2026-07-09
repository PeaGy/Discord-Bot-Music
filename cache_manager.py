import os
import hashlib
import asyncio
import yt_dlp
import discord

# Tạo thư mục cache
CACHE_DIR = "audio_cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

def get_cache_filename(url):
    # Dùng MD5 để tạo tên file độc nhất từ URL (Bản dịch từ crypto.createHash của JS)
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
    return os.path.join(CACHE_DIR, f"track_{url_hash}.mp3")

def download_audio_sync(url, filepath):
    """Hàm chạy đồng bộ để tải nhạc qua yt-dlp"""
    # Xóa đuôi .mp3 đi vì yt-dlp sẽ tự động gắn đuôi sau khi xử lý
    base_path = filepath.rsplit('.', 1)[0]
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': base_path + '.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'quiet': True,
        'nocheckcertificate': True
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    
    return filepath

async def get_audio_source(url):
    """Hàm chính để xuất ra FFmpegPCMAudio từ luồng Local"""
    filepath = get_cache_filename(url)

    # 1. Kiểm tra Cache Hit (File đã tồn tại và không bị lỗi 0 byte)
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        print(f"🎵 [CACHE HIT] Đang phát file cục bộ: {filepath}")
        # Thêm các options đệm nhỏ để đọc file mượt hơn
        return discord.FFmpegPCMAudio(filepath, options="-vn -b:a 128k")

    print(f"⬇️ [CACHE MISS] Bắt đầu tải và lưu trữ: {url}")
    # 2. Tải ngầm bằng asyncio (Giúp lệnh /play không bị treo)
    await asyncio.to_thread(download_audio_sync, url, filepath)
    
    print(f"✅ Đã lưu Cache thành công, bắt đầu phát!")
    return discord.FFmpegPCMAudio(filepath, options="-vn -b:a 128k")