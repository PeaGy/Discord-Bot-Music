import yt_dlp

ydl_opts = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "extractor_args": {"youtube": ["player_client=ios,android,web"]},
}
ydl = yt_dlp.YoutubeDL(ydl_opts)
try:
    result = ydl.extract_info("ytsearch1:hindia secukupnya", download=False)
    print(result["entries"][0]["title"])
except Exception as exc:
    print("ERROR:", exc)

