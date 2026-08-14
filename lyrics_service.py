import os
import re
import time
import urllib.parse

import aiohttp
from openai import AsyncOpenAI

_cache = {}


def _clean(text):
    return re.sub(r"^\[\d{1,2}:\d{2}(?:\.\d+)?\]\s*", "", text or "", flags=re.MULTILINE).strip()


async def fetch_lyrics(title, artist=""):
    key = (title.casefold(), artist.casefold())
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    clean_title = re.sub(r"\(.*?\)|\[.*?\]|(?i:official|music video|lyric video|audio|video)", "", title)
    clean_artist = re.sub(r"(?i:official|vevo|topic|- topic)", "", artist or "")
    query = urllib.parse.quote(f"{clean_title} {clean_artist}".strip())
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://lrclib.net/api/search?q={query}", headers={"User-Agent": "Discord-Music-Bot/1.0"}, timeout=15) as response:
            if response.status != 200: return None
            data = await response.json()
    lyrics = _clean((data[0].get("plainLyrics") or data[0].get("syncedLyrics"))) if data else None
    _cache[key] = (time.time(), lyrics)
    return lyrics


async def translate_lyrics(lyrics, language):
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("Cần XAI_API_KEY để dịch lời bài hát.")
    client = AsyncOpenAI(api_key=api_key, base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1"))
    response = await client.chat.completions.create(
        model=os.getenv("XAI_MODEL", "grok-4.6"), temperature=0.2,
        messages=[{"role": "system", "content": f"Translate these song lyrics into {language}. Preserve line breaks and return only the translation."},
                  {"role": "user", "content": lyrics[:12000]}],
    )
    return response.choices[0].message.content.strip()
