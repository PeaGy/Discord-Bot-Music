from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
import time


logger = logging.getLogger(__name__)

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
        direct.pop("proxy", None)
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
            if result_validator is None or result_validator(result):
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
