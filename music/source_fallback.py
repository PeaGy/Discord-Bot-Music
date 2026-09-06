from __future__ import annotations

from difflib import SequenceMatcher
import html
import json
import logging
import re
import threading
import time
import unicodedata
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import yt_dlp

from ytdlp_support import (
    audius_fallback_enabled,
    soundcloud_fallback_ttl_seconds,
    soundcloud_fallback_enabled,
    soundcloud_ydl_options,
)


logger = logging.getLogger(__name__)

_SEARCH_RESULT_LIMIT = 5
_RESOLVE_ATTEMPT_LIMIT = 3
_MINIMUM_MATCH_SCORE = 0.72
_AUDIUS_API_BASE = "https://api.audius.co/v1"
_AUDIUS_APP_NAME = "Peto"
_AUDIUS_SEARCH_RESULT_LIMIT = 10
_AUDIUS_HTTP_TIMEOUT_SECONDS = 8
_AUDIUS_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_AUDIUS_USER_AGENT = "Peto Discord Bot/1.0"
_VERSION_MARKERS = (
    "acoustic",
    "cover",
    "instrumental",
    "karaoke",
    "live",
    "nightcore",
    "remix",
    "reverb",
    "slowed",
    "sped up",
)

_match_cache: dict[str, tuple[float, str]] = {}
_audius_match_cache: dict[str, tuple[float, str]] = {}
_match_cache_lock = threading.Lock()


def _normalize_text(value: object) -> str:
    text = html.unescape(str(value or "")).casefold().replace("&", " and ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _meaningful_author(value: object) -> str:
    normalized = _normalize_text(value)
    return "" if normalized in {"", "unknown", "unknown artist"} else normalized


def _text_similarity(expected: str, actual: str) -> float:
    if not expected or not actual:
        return 0.0
    expected_tokens = set(expected.split())
    actual_tokens = set(actual.split())
    common_tokens = expected_tokens & actual_tokens
    coverage = len(common_tokens) / max(1, min(len(expected_tokens), len(actual_tokens)))
    sequence = SequenceMatcher(None, expected, actual).ratio()
    if expected in actual or actual in expected:
        coverage = max(coverage, 0.96)
    if min(len(expected_tokens), len(actual_tokens)) >= 2 and len(common_tokens) < 2:
        coverage *= 0.55
    return max(sequence, coverage)


def _has_unrequested_version(original_title: str, candidate_title: str) -> bool:
    original_padded = f" {original_title} "
    candidate_padded = f" {candidate_title} "
    return any(
        f" {marker} " in candidate_padded and f" {marker} " not in original_padded
        for marker in _VERSION_MARKERS
    )


def score_soundcloud_candidate(song: dict, candidate: dict) -> float | None:
    """Score a SoundCloud search result without making any network requests."""
    original_title = _normalize_text(song.get("title"))
    candidate_title = _normalize_text(candidate.get("title"))
    if not original_title or not candidate_title:
        return None
    if _has_unrequested_version(original_title, candidate_title):
        return None

    title_score = _text_similarity(original_title, candidate_title)
    if title_score < 0.62:
        return None

    original_author = _meaningful_author(song.get("author"))
    candidate_author = _normalize_text(
        " ".join(
            str(candidate.get(key) or "")
            for key in ("artist", "creator", "uploader", "channel", "title")
        )
    )
    if original_author:
        author_score = _text_similarity(original_author, candidate_author)
        if author_score < 0.34:
            return None
    else:
        author_score = 0.55

    try:
        original_duration = float(song.get("duration") or 0)
        candidate_duration = float(candidate.get("duration") or 0)
    except (TypeError, ValueError):
        original_duration = candidate_duration = 0

    if original_duration > 0 and candidate_duration > 0:
        tolerance = max(15.0, original_duration * 0.08)
        duration_delta = abs(original_duration - candidate_duration)
        if duration_delta > tolerance:
            return None
        duration_score = max(0.0, 1.0 - duration_delta / tolerance)
    else:
        duration_score = 0.45

    return 0.68 * title_score + 0.20 * author_score + 0.12 * duration_score


def _cache_key(song: dict) -> str:
    try:
        duration_bucket = round(float(song.get("duration") or 0) / 5)
    except (TypeError, ValueError):
        duration_bucket = 0
    return "|".join(
        (
            _normalize_text(song.get("title")),
            _meaningful_author(song.get("author")),
            str(duration_bucket),
        )
    )


def get_cached_soundcloud_page(song: dict) -> str | None:
    key = _cache_key(song)
    now = time.monotonic()
    with _match_cache_lock:
        cached = _match_cache.get(key)
        if not cached:
            return None
        expires_at, page_url = cached
        if expires_at <= now:
            _match_cache.pop(key, None)
            return None
        return page_url


def _remember_soundcloud_page(song: dict, page_url: str) -> None:
    expires_at = time.monotonic() + soundcloud_fallback_ttl_seconds()
    with _match_cache_lock:
        _match_cache[_cache_key(song)] = (expires_at, page_url)


def _forget_soundcloud_page(song: dict) -> None:
    with _match_cache_lock:
        _match_cache.pop(_cache_key(song), None)


def clear_soundcloud_fallback_cache() -> None:
    """Clear volatile audio match hints; primarily useful for tests."""
    with _match_cache_lock:
        _match_cache.clear()
        _audius_match_cache.clear()


def get_cached_audius_track_id(song: dict) -> str | None:
    key = _cache_key(song)
    now = time.monotonic()
    with _match_cache_lock:
        cached = _audius_match_cache.get(key)
        if not cached:
            return None
        expires_at, track_id = cached
        if expires_at <= now:
            _audius_match_cache.pop(key, None)
            return None
        return track_id


def _remember_audius_track(song: dict, track_id: str) -> None:
    expires_at = time.monotonic() + soundcloud_fallback_ttl_seconds()
    with _match_cache_lock:
        _audius_match_cache[_cache_key(song)] = (expires_at, track_id)


def _forget_audius_track(song: dict) -> None:
    with _match_cache_lock:
        _audius_match_cache.pop(_cache_key(song), None)


def _soundcloud_page_url(entry: dict) -> str | None:
    for key in ("webpage_url", "original_url", "url"):
        value = str(entry.get(key) or "").strip()
        if not value.startswith(("https://", "http://")):
            continue
        hostname = (urlparse(value).hostname or "").casefold()
        if hostname == "soundcloud.com" or hostname.endswith(".soundcloud.com"):
            return value
    return None


def _resolve_soundcloud_page(page_url: str) -> dict:
    options = soundcloud_ydl_options(
        {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 20,
            "retries": 1,
            "extractor_retries": 1,
        }
    )
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(page_url, download=False)
    if "entries" in info:
        info = next((entry for entry in info.get("entries") or [] if entry), None)
    if not info or not info.get("url"):
        raise ValueError("SoundCloud không trả về stream URL")
    return {
        "stream_url": info["url"],
        "webpage_url": info.get("webpage_url") or page_url,
        "title": info.get("title"),
        "artist": info.get("artist") or info.get("uploader") or info.get("creator"),
        "duration": info.get("duration"),
        "http_headers": dict(info.get("http_headers") or {}),
    }


def _search_soundcloud_candidates(song: dict) -> list[tuple[float, str]]:
    title = str(song.get("title") or "").strip()
    author = str(song.get("author") or "").strip()
    query = " ".join(part for part in (title, author) if part)
    if not query:
        return []

    options = soundcloud_ydl_options(
        {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "playlistend": _SEARCH_RESULT_LIMIT,
            "socket_timeout": 20,
            "retries": 1,
            "extractor_retries": 1,
        }
    )
    with yt_dlp.YoutubeDL(options) as ydl:
        result = ydl.extract_info(
            f"scsearch{_SEARCH_RESULT_LIMIT}:{query}",
            download=False,
        )

    candidates = []
    for entry in (result or {}).get("entries") or []:
        if not entry:
            continue
        page_url = _soundcloud_page_url(entry)
        score = score_soundcloud_candidate(song, entry)
        if page_url and score is not None and score >= _MINIMUM_MATCH_SCORE:
            candidates.append((score, page_url))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates


def resolve_soundcloud_fallback_sync(
    song: dict,
    *,
    preferred_page_url: str | None = None,
) -> dict | None:
    """Find and freshly resolve a close SoundCloud match for direct streaming."""
    cached_page_url = preferred_page_url or get_cached_soundcloud_page(song)
    attempted_urls = set()
    if cached_page_url:
        attempted_urls.add(cached_page_url)
        try:
            resolved = _resolve_soundcloud_page(cached_page_url)
            _remember_soundcloud_page(song, resolved["webpage_url"])
            return resolved
        except Exception as error:
            _forget_soundcloud_page(song)
            logger.info("SoundCloud fallback cache đã cũ, tìm lại: %s", error)

    try:
        candidates = _search_soundcloud_candidates(song)
    except Exception as error:
        logger.warning("Không tìm được SoundCloud fallback: %s", error)
        return None

    for _score, page_url in candidates[:_RESOLVE_ATTEMPT_LIMIT]:
        if page_url in attempted_urls:
            continue
        attempted_urls.add(page_url)
        try:
            resolved = _resolve_soundcloud_page(page_url)
        except Exception as error:
            logger.info("Không resolve được SoundCloud candidate %s: %s", page_url, error)
            continue
        _remember_soundcloud_page(song, resolved["webpage_url"])
        return resolved
    return None


def _audius_api_data(path: str, params: dict | None = None):
    query = urlencode({**(params or {}), "app_name": _AUDIUS_APP_NAME})
    url = f"{_AUDIUS_API_BASE}{path}"
    if query:
        url = f"{url}?{query}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": _AUDIUS_USER_AGENT,
        },
    )
    with urlopen(request, timeout=_AUDIUS_HTTP_TIMEOUT_SECONDS) as response:
        raw = response.read(_AUDIUS_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _AUDIUS_MAX_RESPONSE_BYTES:
        raise ValueError("Audius trả về response quá lớn")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or "data" not in payload:
        raise ValueError("Audius trả về response không hợp lệ")
    return payload["data"]


def _audius_page_url(track: dict) -> str | None:
    value = str(track.get("permalink") or "").strip()
    if value.startswith("/"):
        value = f"https://audius.co{value}"
    if not value.startswith(("https://", "http://")):
        return None
    hostname = (urlparse(value).hostname or "").casefold()
    if hostname == "audius.co" or hostname.endswith(".audius.co"):
        return value
    return None


def _audius_stream_is_available(track: dict) -> bool:
    if track.get("is_stream_gated"):
        return False
    access = track.get("access")
    return not isinstance(access, dict) or access.get("stream") is not False


def _audius_candidate(track: dict) -> dict:
    user = track.get("user") if isinstance(track.get("user"), dict) else {}
    artist = user.get("name") or user.get("handle")
    return {
        "title": track.get("title"),
        "artist": artist,
        "uploader": artist,
        "duration": track.get("duration"),
    }


def _audius_resolved_result(track: dict) -> dict | None:
    track_id = str(track.get("id") or "").strip()
    page_url = _audius_page_url(track)
    if not track_id or not page_url or not _audius_stream_is_available(track):
        return None
    user = track.get("user") if isinstance(track.get("user"), dict) else {}
    stream_query = urlencode({"app_name": _AUDIUS_APP_NAME})
    return {
        "stream_url": (
            f"{_AUDIUS_API_BASE}/tracks/{track_id}/stream?{stream_query}"
        ),
        "webpage_url": page_url,
        "title": track.get("title"),
        "artist": user.get("name") or user.get("handle"),
        "duration": track.get("duration"),
        "http_headers": {"User-Agent": _AUDIUS_USER_AGENT},
        "track_id": track_id,
    }


def resolve_audius_fallback_sync(
    song: dict,
    *,
    preferred_track_id: str | None = None,
) -> dict | None:
    """Find a close Audius match and return its stable direct-stream endpoint."""
    cached_track_id = preferred_track_id or get_cached_audius_track_id(song)
    if cached_track_id:
        try:
            track = _audius_api_data(f"/tracks/{cached_track_id}")
            resolved = _audius_resolved_result(track) if isinstance(track, dict) else None
            if resolved:
                _remember_audius_track(song, resolved["track_id"])
                return resolved
        except Exception as error:
            logger.info("Audius fallback cache đã cũ, tìm lại: %s", error)
        _forget_audius_track(song)

    title = str(song.get("title") or "").strip()
    author = str(song.get("author") or "").strip()
    query = " ".join(part for part in (title, author) if part)
    if not query:
        return None

    try:
        tracks = _audius_api_data(
            "/tracks/search",
            {
                "query": query,
                "limit": _AUDIUS_SEARCH_RESULT_LIMIT,
                "sort_method": "relevant",
            },
        )
    except Exception as error:
        logger.warning("Không tìm được Audius fallback: %s", error)
        return None

    candidates = []
    for track in tracks if isinstance(tracks, list) else []:
        if not isinstance(track, dict) or not _audius_stream_is_available(track):
            continue
        score = score_soundcloud_candidate(song, _audius_candidate(track))
        resolved = _audius_resolved_result(track)
        if score is not None and score >= _MINIMUM_MATCH_SCORE and resolved:
            candidates.append((score, resolved))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    resolved = candidates[0][1]
    _remember_audius_track(song, resolved["track_id"])
    return resolved


def resolve_audio_fallback_sync(
    song: dict,
    *,
    preferred_source: str | None = None,
    preferred_locator: str | None = None,
) -> dict | None:
    """Try enabled direct-stream providers in SoundCloud -> Audius order."""
    providers = []
    if soundcloud_fallback_enabled():
        providers.append("soundcloud")
    if audius_fallback_enabled():
        providers.append("audius")

    preferred_source = str(preferred_source or "").casefold()
    if preferred_source in providers:
        providers.remove(preferred_source)
        providers.insert(0, preferred_source)

    for provider in providers:
        if provider == "soundcloud":
            result = resolve_soundcloud_fallback_sync(
                song,
                preferred_page_url=(
                    preferred_locator if preferred_source == provider else None
                ),
            )
        else:
            result = resolve_audius_fallback_sync(
                song,
                preferred_track_id=(
                    preferred_locator if preferred_source == provider else None
                ),
            )
        if result:
            result["fallback_source"] = provider
            result["fallback_locator"] = (
                result.get("track_id")
                if provider == "audius"
                else result.get("webpage_url")
            )
            return result
        logger.info(
            "%s không có bản đủ khớp cho %r",
            provider.capitalize(),
            song.get("title", "Unknown"),
        )
    return None
