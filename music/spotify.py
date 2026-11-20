import urllib.request
import logging
import re
from urllib.parse import urlsplit
import music_library


logger = logging.getLogger(__name__)

def is_spotify_url(query: str) -> bool:
    return "open.spotify.com" in query or "spotify:" in query


def spotify_track_id(url):
    text = url.strip()
    match = re.fullmatch(r"spotify:track:([A-Za-z0-9]{22})", text)
    if not match:
        parsed = urlsplit(text)
        if parsed.netloc.lower() != "open.spotify.com" or parsed.scheme not in ("https", "http"):
            return None
        match = re.fullmatch(r"/(?:intl-[\w-]+/)?track/([A-Za-z0-9]{22})/?", parsed.path)
    return match[1] if match else None

def get_spotify_info(url: str) -> dict:
    try:
        track_id = spotify_track_id(url)
        if track_id:
            known = music_library.cached_search_match("spotify:" + track_id)
            if known:
                return {**known, "url": url, "cached_youtube_url": known["url"], "source": "spotify",
                        "search_query": f"{known['title']} {known['author']} audio"}
        if "track" not in url:
            raise ValueError("Only Spotify tracks are supported right now.")

        page = f"https://open.spotify.com/track/{track_id}" if track_id else url
        req = urllib.request.Request(page, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req).read().decode('utf-8')

        title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        desc_match = re.search(r'<meta property="og:description" content="([^"]+)"', html)
        image_match = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        duration_match = re.search(r'<meta name="music:duration" content="(\d+)"', html)

        title = title_match.group(1) if title_match else "Unknown Title"
        
        artist = "Unknown Artist"
        if desc_match:
            desc = desc_match.group(1)
            parts = desc.split(" · ")
            if len(parts) > 0:
                artist = parts[0]

        thumbnail = image_match.group(1) if image_match else None
        duration = int(duration_match.group(1)) if duration_match else 0
        
        return {
            "title": f"{title}",
            "author": artist,
            "duration": duration,
            "thumbnail": thumbnail,
            "url": url,
            "source": "spotify",
            "search_query": f"{title} {artist} audio"
        }
    except Exception as e:
        logger.warning("Không lấy được metadata Spotify: %s", e)
        return None
