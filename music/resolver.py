"""Resolve a music request to track metadata, using local audio when possible."""

import logging

import music_library
from cache_manager import find_cached_audio_path
from music.spotify import get_spotify_info, is_spotify_url
from music.urls import canonical_youtube_url
from ytdlp_support import (
    audio_fallback_enabled,
    extract_info_with_retry,
    is_transient_ytdlp_error,
    youtube_direct_fallback_enabled,
    youtube_ydl_options,
)

logger = logging.getLogger(__name__)


def resolve_song(query: str) -> dict | None:
    if is_spotify_url(query):
        return get_spotify_info(query)
    return get_song_info(query)


def is_soundcloud_url(text):
    return "soundcloud.com" in text


def is_youtube_url(text):
    return "youtube.com" in text or "youtu.be" in text


def _has_playable_audio_format(info: dict) -> bool:
    return any(
        item.get("url") and str(item.get("acodec") or "").casefold() != "none"
        for item in info.get("formats") or []
    )


def _extraction_has_playable_audio_or_cache(info: dict | None) -> bool:
    if not info:
        return False
    if "entries" in info:
        info = next((entry for entry in info["entries"] if entry), None)
    if not info:
        return False
    url = info.get("webpage_url") or ""
    return bool(
        _has_playable_audio_format(info)
        or (canonical_youtube_url(url) and find_cached_audio_path(url))
    )


def _audio_fallback_search_seed(query: str) -> dict:
    """Keep a failed keyword search playable without inventing metadata."""
    return {
        "title": query.strip(),
        "author": "Unknown",
        "duration": 0,
        "url": query.strip(),
        "thumbnail": None,
        "source": "youtube",
        "youtube_metadata_failed": True,
    }


def get_song_info(query: str):
    canonical = canonical_youtube_url(query)
    keyword_key = None
    if query.strip() and "://" not in query and not is_youtube_url(query) and not is_soundcloud_url(query):
        keyword_key = "keyword:" + " ".join(query.casefold().split())
        remembered = music_library.cached_search_match(keyword_key)
        if remembered:
            return {**remembered, "source": "youtube"}
    if canonical and find_cached_audio_path(query):
        known = music_library.find_known_track_sync(canonical) or {}
        logger.info("Metadata từ cache cục bộ, bỏ qua YouTube: %s", canonical)
        return {
            "title": known.get("title") or f"YouTube (bản đã lưu: {canonical.split('=')[-1]})",
            "author": known.get("author") or "Unknown",
            "duration": known.get("duration") or 0,
            # Preserve an exact legacy URL cache hit; cache_manager also tries
            # the canonical URL when the user supplies a share/mobile link.
            "url": query.strip(),
            "thumbnail": known.get("thumbnail"),
            "source": "youtube",
        }
    fallback_eligible = (
        audio_fallback_enabled() and not is_soundcloud_url(query)
    )
    options = youtube_ydl_options(
        {"format": "bestaudio/best", "quiet": True, "noplaylist": True}
    )
    direct_fallback_eligible = bool(
        not is_soundcloud_url(query)
        and options.get("proxy")
        and youtube_direct_fallback_enabled()
    )
    metadata_tolerant = fallback_eligible or direct_fallback_eligible
    if metadata_tolerant:
        # A bot-check response often still contains title/author/thumbnail in
        # the initial page data. Keep that metadata even when no audio format is
        # available. The shared retry helper first tries direct VPS egress, then
        # preserves the metadata for external providers if both routes fail.
        options["ignore_no_formats_error"] = True
    lookup = (
        query
        if is_soundcloud_url(query) or is_youtube_url(query)
        else f"ytsearch1:{query}"
    )
    try:
        info = extract_info_with_retry(
            lookup,
            options,
            download=False,
            remember_for_download=not is_soundcloud_url(query),
            result_validator=(
                _extraction_has_playable_audio_or_cache if metadata_tolerant else None
            ),
        )
    except Exception as error:
        # A plain-text query already is useful SoundCloud search metadata. A
        # failed direct YouTube URL is not: never search using only a video ID.
        if (
            fallback_eligible
            and not is_youtube_url(query)
            and is_transient_ytdlp_error(error)
        ):
            logger.info(
                "YouTube search bị chặn; chuyển query %r vào audio fallback",
                query,
            )
            return _audio_fallback_search_seed(query)
        raise
    if info and "entries" in info:
        info = next((entry for entry in info["entries"] if entry), None)
    if not info:
        raise ValueError("Không tìm thấy kết quả phù hợp.")
    song = {
        "title": info["title"],
        "author": (
            info.get("uploader")
            or info.get("creator")
            or info.get("channel", "Unknown")
        ),
        "duration": info.get("duration", 0),
        "url": info.get("webpage_url") or query,
        "thumbnail": info.get("thumbnail"),
        "source": "soundcloud" if is_soundcloud_url(query) else "youtube",
    }
    # A keyword lookup may identify a cached video on its very first response,
    # even while YouTube refuses audio. No further client/proxy retries needed.
    cached_audio = bool(canonical_youtube_url(song["url"]) and find_cached_audio_path(song["url"]))
    if fallback_eligible and not _has_playable_audio_format(info) and not cached_audio:
        song["youtube_metadata_failed"] = True
        logger.info(
            "YouTube chỉ trả metadata cho %r; chuyển sang audio fallback",
            song["title"],
        )
    elif direct_fallback_eligible and not _has_playable_audio_format(info) and not cached_audio:
        raise ValueError("YouTube không trả về định dạng audio có thể phát.")
    if keyword_key and (cached_audio or _has_playable_audio_format(info)):
        music_library.remember_search_match(keyword_key, song["url"], song)
    return song
