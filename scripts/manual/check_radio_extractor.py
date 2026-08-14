import yt_dlp

ydl_opts = {"format": "bestaudio/best", "quiet": False, "noplaylist": True}
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    try:
        info = ydl.extract_info("http://radio.plaza.one/mp3", download=False)
        print("Success!", info.get("url"))
    except Exception as exc:
        print("Error:", exc)

