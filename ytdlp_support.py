from __future__ import annotations

import copy
from collections import OrderedDict
from dataclasses import dataclass, field
import logging
import math
import os
from pathlib import Path
import threading
import time

from music.urls import canonical_youtube_url


logger = logging.getLogger(__name__)

# A download handoff, not a persistent metadata cache. Signed CDN URLs and
# cookies must never be copied into song dictionaries, SQLite, or logs.
_DOWNLOAD_SEED_TTL_SECONDS = 120.0
_DOWNLOAD_SEED_MAX_ENTRIES = 32
_DOWNLOAD_SESSION_KEYS = (
    "proxy", "source_address", "cookiefile", "cookiesfrombrowser",
    "extractor_args", "http_headers", "geo_verification_proxy",
    "username", "password", "client_certificate", "client_certificate_key",
    "client_certificate_password", "js_runtimes", "remote_components", "impersonate",
)
_DOWNLOAD_SESSION_ENV = (
    "YTDLP_PROXY", "YTDLP_YOUTUBE_DIRECT_FALLBACK", "YTDLP_YOUTUBE_CLIENT",
    "YTDLP_COOKIE_FILE", "YTDLP_JS_RUNTIME", "YTDLP_REMOTE_COMPONENTS",
    "YTDLP_BGUTIL_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)


@dataclass
class YoutubeDownloadSeed:
    """Single-use resolved video/session; sensitive fields are deliberately hidden."""

    info: dict = field(repr=False)
    options: dict = field(repr=False)
    route_name: str
    player_client: str | None
    cookiejar: object = field(repr=False)


@dataclass(repr=False)
class _DownloadSeedEntry:
    seed: YoutubeDownloadSeed
    created_at: float
    configuration: object
    cookie_file_state: object


_download_seeds: OrderedDict[str, _DownloadSeedEntry] = OrderedDict()
_download_seeds_lock = threading.Lock()


def _download_session_configuration(options: dict):
    return (
        copy.deepcopy({key: options[key] for key in _DOWNLOAD_SESSION_KEYS if key in options}),
        tuple((key, os.getenv(key)) for key in _DOWNLOAD_SESSION_ENV),
    )


def _cookie_file_state(options: dict):
    """Detect a replaced cookie file without reading or logging its contents."""
    path = options.get("cookiefile")
    if not isinstance(path, (str, os.PathLike)) or not path:
        return None
    try:
        stat = Path(path).stat()
        return (stat.st_size, stat.st_mtime_ns, stat.st_ino)
    except OSError:
        return None


def _prune_download_seeds(now: float) -> None:
    for key, entry in list(_download_seeds.items()):
        if now - entry.created_at >= _DOWNLOAD_SEED_TTL_SECONDS:
            _download_seeds.pop(key, None)


def _capture_download_seed(query, result, ydl, attempt_options, route_name, client):
    if not isinstance(result, dict):
        return None
    info = result
    if "entries" in info:
        entries = info.get("entries")
        # /play uses ytsearch1. Never select an arbitrary item from a playlist.
        if not str(query).strip().casefold().startswith("ytsearch1:"):
            return None
        if not isinstance(entries, (list, tuple)) or len(entries) != 1:
            return None
        info = entries[0]
    if not isinstance(info, dict) or info.get("_type", "video") != "video":
        return None
    canonical = canonical_youtube_url(info.get("webpage_url") or "")
    if not canonical or str(info.get("id") or "") != canonical.split("=")[-1]:
        return None
    query_url = canonical_youtube_url(query)
    if query_url != canonical and not str(query).strip().casefold().startswith("ytsearch1:"):
        return None
    if info.get("is_live") or info.get("live_status") not in (None, "not_live"):
        return None
    try:
        duration = float(info.get("duration") or 0)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(duration) or not 0 < duration <= 600:
        return None
    formats = info.get("formats")
    if not isinstance(formats, (list, tuple)) or not any(
        isinstance(item, dict) and item.get("url")
        and item.get("acodec") not in (None, "", "none")
        for item in formats
    ):
        return None
    if info.get("additional_urls") or "entries" in info:
        return None

    resolved = copy.deepcopy(info)
    # A new caller supplies its own output template and format selection. Keep
    # the complete format catalog, not old selected-download/file bookkeeping.
    for key in list(resolved):
        if key.startswith("__") or key in {
            "requested_downloads", "requested_formats", "requested_subtitles",
            "filepath", "_filename", "filename", "infojson_filename",
        }:
            resolved.pop(key, None)
    session_options = copy.deepcopy({
        key: attempt_options[key]
        for key in _DOWNLOAD_SESSION_KEYS if key in attempt_options
    })
    # yt-dlp may supply its own default headers. Preserve those along with the
    # per-format headers already present in the resolved result.
    if getattr(ydl, "params", {}).get("http_headers"):
        session_options["http_headers"] = copy.deepcopy(ydl.params["http_headers"])
    original_jar = ydl.cookiejar
    jar = type(original_jar)()
    for cookie in original_jar:
        jar.set_cookie(copy.deepcopy(cookie))
    # This clone has no disk filename. The download must install it before any
    # request; it must neither reload browser cookies nor overwrite a cookiefile.
    session_options.pop("cookiefile", None)
    session_options.pop("cookiesfrombrowser", None)
    return canonical, YoutubeDownloadSeed(resolved, session_options, route_name, client, jar)


def take_youtube_download_seed(url: str, base_options: dict) -> YoutubeDownloadSeed | None:
    """Consume a fresh, matching session handoff, or use normal extraction.

    Call only when the download is ready to start, after waiting for the cache
    worker. The caller must assign ``ydl.cookiejar = seed.cookiejar`` before
    ``ydl.process_ie_result(seed.info, download=True)``. Never serialize a seed.
    """
    canonical = canonical_youtube_url(url)
    if not canonical:
        return None
    now = time.monotonic()
    with _download_seeds_lock:
        _prune_download_seeds(now)
        entry = _download_seeds.pop(canonical, None)
    if entry is None:
        return None
    if entry.configuration != _download_session_configuration(base_options):
        return None
    if entry.cookie_file_state != _cookie_file_state(base_options):
        return None
    options = copy.deepcopy(base_options)
    for key in _DOWNLOAD_SESSION_KEYS:
        options.pop(key, None)
    options.update(entry.seed.options)
    entry.seed.options = options
    return entry.seed

_TRANSIENT_YTDLP_ERROR_MARKERS = (
    "http error 403",
    "403 forbidden",
    "http error 429",
    "too many requests",
    "sign in to confirm you're not a bot",
    "sign in to confirm you’re not a bot",
    "missing required visitor data",
    "host unreachable",
    "network is unreachable",
    "socks5error",
    "connection reset",
    "connection aborted",
    "remote end closed",
    "temporarily unavailable",
    "timed out",
    "timeout",
)


def youtube_proxy_enabled() -> bool:
    """Return whether this deployment routes yt-dlp through a dedicated proxy."""
    return bool(os.getenv("YTDLP_PROXY", "").strip())


def youtube_direct_fallback_enabled() -> bool:
    """Return whether a failed proxied YouTube request may retry directly."""
    return os.getenv("YTDLP_YOUTUBE_DIRECT_FALLBACK", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def youtube_player_clients() -> tuple[str, ...]:
    """Return the ordered YouTube client fallback list for this deployment."""
    configured = os.getenv("YTDLP_YOUTUBE_CLIENT", "").strip().lower()
    if configured:
        return tuple(
            item.strip()
            for item in configured.split(",")
            if item.strip()
        )
    if os.getenv("YTDLP_BGUTIL_URL", "").strip():
        return ("web_embedded", "mweb")
    return ()


def should_use_long_audio_temp(
    duration: int | float | None,
    *,
    is_radio: bool = False,
    stream_threshold: int = 600,
) -> bool:
    """Keep home/radio streaming unchanged; bridge proxied long tracks locally."""
    try:
        seconds = int(duration or 0)
    except (TypeError, ValueError):
        seconds = 0
    return bool(
        youtube_proxy_enabled()
        and not is_radio
        and seconds > int(stream_threshold)
    )


def is_transient_ytdlp_error(error: BaseException) -> bool:
    """Recognize network/proxy failures worth one fresh metadata attempt.

    Download helpers wrap the original yt-dlp exception so callers can present a
    friendlier error.  Walk that exception chain as well; otherwise a bot-check
    hidden behind ``AudioDownloadError`` would never reach the source fallback.
    """
    current = error
    seen = set()
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        message = str(current or "").casefold()
        if any(marker in message for marker in _TRANSIENT_YTDLP_ERROR_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def soundcloud_fallback_enabled() -> bool:
    """Return whether automatic YouTube -> SoundCloud rescue is enabled."""
    return os.getenv("YTDLP_SOUNDCLOUD_FALLBACK", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def audius_fallback_enabled() -> bool:
    """Return whether Audius is enabled as the last direct-stream fallback."""
    return os.getenv("YTDLP_AUDIUS_FALLBACK", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def audio_fallback_enabled() -> bool:
    """Return whether at least one non-YouTube audio fallback is enabled."""
    return soundcloud_fallback_enabled() or audius_fallback_enabled()


def soundcloud_fallback_ttl_seconds() -> int:
    """Return the bounded lifetime of an in-memory matched-track hint."""
    raw_value = os.getenv("YTDLP_SOUNDCLOUD_FALLBACK_TTL_SECONDS", "1800")
    try:
        return max(60, min(int(raw_value), 6 * 60 * 60))
    except (TypeError, ValueError):
        return 1800


def soundcloud_fallback_timeout_seconds() -> float:
    """Return the maximum time playback may wait for a fallback match."""
    raw_value = os.getenv("YTDLP_SOUNDCLOUD_FALLBACK_TIMEOUT_SECONDS", "20")
    try:
        return max(5.0, min(float(raw_value), 60.0))
    except (TypeError, ValueError):
        return 20.0


def audio_fallback_timeout_seconds() -> float:
    """Return one shared timeout for the SoundCloud -> Audius fallback chain.

    Keep the old SoundCloud variable as a backwards-compatible fallback so an
    existing VPS does not silently change its wait time after an update.
    """
    raw_value = os.getenv("YTDLP_AUDIO_FALLBACK_TIMEOUT_SECONDS", "").strip()
    if not raw_value:
        return soundcloud_fallback_timeout_seconds()
    try:
        return max(5.0, min(float(raw_value), 60.0))
    except (TypeError, ValueError):
        return 20.0


def soundcloud_ydl_options(base: dict | None = None) -> dict:
    """Build yt-dlp options for a directly streamed SoundCloud fallback.

    This path intentionally does not inherit ``YTDLP_PROXY`` or YouTube-only
    extractor arguments.  FFmpeg consumes the returned CDN URL directly, so
    resolving it through a different proxy egress could make the URL unusable.
    """
    options = dict(base or {})
    options.pop("proxy", None)
    options.pop("extractor_args", None)
    options.pop("cookiefile", None)
    return options


def ydl_options_for_player_client(options: dict, player_client: str | None) -> dict:
    """Return an isolated option set that forces one YouTube client."""
    attempt_options = copy.deepcopy(options)
    if not player_client:
        return attempt_options
    extractor_args = dict(attempt_options.get("extractor_args") or {})
    youtube_args = dict(extractor_args.get("youtube") or {})
    youtube_args["player_client"] = [str(player_client)]
    extractor_args["youtube"] = youtube_args
    attempt_options["extractor_args"] = extractor_args
    return attempt_options


def youtube_ydl_routes(
    options: dict,
    *,
    query: str | None = None,
) -> tuple[tuple[str, dict], ...]:
    """Build the bounded YouTube egress order for one operation.

    The normal route keeps ``YTDLP_PROXY`` (WARP on the VPS).  When explicitly
    enabled, a transient failure gets one second route with the proxy removed,
    so the request exits through the VPS address.  Cookies are deliberately not
    copied to that route: public music does not require them, and reusing one
    account session from two IPs is an unnecessary risk.

    ``extract_info_with_retry`` can also receive a SoundCloud URL, so only add
    the direct route when its query is recognisably YouTube-backed.
    """
    primary = copy.deepcopy(options)
    primary_name = "proxy" if primary.get("proxy") else "direct"
    routes = [(primary_name, primary)]

    normalized_query = str(query or "").strip().casefold()
    is_youtube_query = (
        not normalized_query
        or normalized_query.startswith("ytsearch")
        or "youtube.com" in normalized_query
        or "youtu.be" in normalized_query
    )
    if (
        primary.get("proxy")
        and is_youtube_query
        and youtube_direct_fallback_enabled()
    ):
        direct = copy.deepcopy(primary)
        # Explicitly disable ambient HTTP(S)_PROXY too. Omitting this option
        # lets yt-dlp silently select another proxy, so the route isn't direct.
        direct["proxy"] = ""
        direct.pop("cookiefile", None)
        direct.pop("cookiesfrombrowser", None)
        routes.append(("direct", direct))

    return tuple(routes)


def extract_info_with_retry(
    query: str,
    options: dict,
    *,
    download: bool = False,
    attempts: int = 2,
    retry_delay: float = 5.0,
    result_validator=None,
    remember_for_download: bool = False,
):
    """Extract metadata while rotating clients and an optional direct route.

    A validator lets callers treat metadata-only YouTube responses as an
    unsuccessful attempt without discarding their title/author.  If no attempt
    becomes usable, the last such response is returned for the external audio
    fallback to consume.
    """
    import yt_dlp

    attempts = max(1, int(attempts))
    clients = youtube_player_clients()
    routes = youtube_ydl_routes(options, query=query)
    attempt_specs = []
    for route_name, route_options in routes:
        for attempt_index in range(attempts):
            client = (
                clients[min(attempt_index, len(clients) - 1)]
                if clients
                else None
            )
            attempt_specs.append((route_name, route_options, client))

    last_error = None
    no_result = object()
    last_unusable_result = no_result
    total_attempts = len(attempt_specs)
    for attempt, (route_name, route_options, client) in enumerate(
        attempt_specs,
        start=1,
    ):
        attempt_options = ydl_options_for_player_client(route_options, client)
        try:
            logger.debug(
                "yt-dlp phase=metadata attempt=%s/%s route=%s client=%s",
                attempt,
                total_attempts,
                route_name,
                client or "auto",
            )
            with yt_dlp.YoutubeDL(attempt_options) as ydl:
                result = ydl.extract_info(query, download=download)
                usable = result_validator is None or result_validator(result)
                captured = None
                if usable and remember_for_download and not download:
                    try:
                        captured = _capture_download_seed(
                            query, result, ydl, attempt_options, route_name, client,
                        )
                    except Exception as capture_error:
                        # Handoff is an optimization, never a reason to fail a
                        # valid /play. Don't include exception text or payloads.
                        logger.debug("Download handoff unavailable (%s)", type(capture_error).__name__)
            if usable:
                if captured is not None:
                    canonical, seed = captured
                    # __exit__ may have saved/rotated a cookiefile: fingerprint
                    # its final stat after closing the metadata session.
                    now = time.monotonic()
                    entry = _DownloadSeedEntry(
                        seed, now, _download_session_configuration(options),
                        _cookie_file_state(options),
                    )
                    with _download_seeds_lock:
                        _prune_download_seeds(now)
                        _download_seeds[canonical] = entry
                        _download_seeds.move_to_end(canonical)
                        while len(_download_seeds) > _DOWNLOAD_SEED_MAX_ENTRIES:
                            _download_seeds.popitem(last=False)
                if route_name == "direct" and len(routes) > 1:
                    logger.info(
                        "YouTube hoạt động qua kết nối trực tiếp sau khi proxy lỗi"
                    )
                return result

            last_unusable_result = result
            if attempt < total_attempts:
                next_route, _, next_client = attempt_specs[attempt]
                logger.info(
                    "yt-dlp phase=metadata không có audio "
                    "(route=%s, client=%s); thử route=%s client=%s",
                    route_name,
                    client or "auto",
                    next_route,
                    next_client or "auto",
                )
        except Exception as error:
            last_error = error
            if attempt >= total_attempts:
                if last_unusable_result is not no_result:
                    return last_unusable_result
                raise

            next_route, _, next_client = attempt_specs[attempt]
            switching_route = next_route != route_name
            switching_client = next_client != client
            transient = is_transient_ytdlp_error(error)
            if switching_route and not transient:
                if last_unusable_result is not no_result:
                    return last_unusable_result
                raise
            if not switching_route and not switching_client and not transient:
                if last_unusable_result is not no_result:
                    return last_unusable_result
                raise
            logger.warning(
                "yt-dlp phase=metadata lỗi (lần %s/%s, route=%s, client=%s): %s "
                "— thử lại route=%s client=%s sau %.1fs",
                attempt,
                total_attempts,
                route_name,
                client or "auto",
                error,
                next_route,
                next_client or client or "auto",
                retry_delay,
            )
            time.sleep(max(0.0, float(retry_delay)))

    if last_unusable_result is not no_result:
        return last_unusable_result
    raise last_error


def youtube_ydl_options(base: dict | None = None) -> dict:
    """Apply optional YouTube proxy and JS settings for VPS deployments."""
    options = dict(base or {})

    proxy = os.getenv("YTDLP_PROXY", "").strip()
    runtime = os.getenv("YTDLP_JS_RUNTIME", "").strip().lower()
    bgutil_url = os.getenv("YTDLP_BGUTIL_URL", "").strip()
    youtube_clients = youtube_player_clients()
    components = {
        item.strip()
        for item in os.getenv("YTDLP_REMOTE_COMPONENTS", "").split(",")
        if item.strip()
    }

    # Preserve the old home-PC route unless this deployment explicitly opts in.
    if not proxy and not runtime and not components and not bgutil_url and not youtube_clients:
        return options

    # On the VPS, let current yt-dlp choose a working YouTube client instead of
    # retaining the older forced-client list.
    options.pop("extractor_args", None)
    options.pop("cookies", None)  # This is not a YoutubeDL API option.

    if proxy:
        options["proxy"] = proxy
    if runtime:
        options["js_runtimes"] = {runtime: {}}
    if components:
        options["remote_components"] = components
    extractor_args = {}
    if youtube_clients or bgutil_url:
        # BgUtils currently supports WEB/MWEB/TV clients, not VISIONOS. Recent
        # yt-dlp builds may auto-select VISIONOS, making the provider reject the
        # request before a PO token can be generated. Prefer the token-free
        # embedded client for public/embeddable videos, then let MWEB cover the
        # rest. The value remains configurable as a comma-separated client list.
        extractor_args["youtube"] = {
            "player_client": list(youtube_clients or ("web_embedded", "mweb")),
        }
    if bgutil_url:
        # The native provider may listen on IPv6 only on some Linux hosts.
        # Point yt-dlp's HTTP provider at the reachable loopback address while
        # leaving the on-demand Node script available as its fallback.
        extractor_args["youtubepot-bgutilhttp"] = {"base_url": [bgutil_url]}
    if extractor_args:
        options["extractor_args"] = extractor_args

    cookie_path = os.getenv("YTDLP_COOKIE_FILE", "cookies.txt").strip()
    if cookie_path and Path(cookie_path).is_file():
        options["cookiefile"] = cookie_path
    else:
        options.pop("cookiefile", None)

    return options
