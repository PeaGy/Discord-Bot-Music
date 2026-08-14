import asyncio

import yt_dlp


def get_song_info():
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "noplaylist": True,
        "extractor_args": {"youtube": ["player_client=ios,android,web"]},
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info("ytsearch1:hindia secukupnya", download=False)
        return result["entries"][0]["title"]


async def main():
    print(await asyncio.to_thread(get_song_info))


if __name__ == "__main__":
    asyncio.run(main())

