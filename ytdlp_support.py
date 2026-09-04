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
    """Recognize network/proxy failures worth one fresh metadata attempt."""
    message = str(error or "").casefold()
    return any(marker in message for marker in _TRANSIENT_YTDLP_ERROR_MARKERS)


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


def extract_info_with_retry(
    query: str,
    options: dict,
    *,
    download: bool = False,
    attempts: int = 2,
    retry_delay: float = 5.0,
):
    """Extract metadata in fresh sessions and rotate configured YouTube clients."""
    import yt_dlp

    attempts = max(1, int(attempts))
    clients = youtube_player_clients()
    last_error = None
    for attempt in range(1, attempts + 1):
        client = clients[min(attempt - 1, len(clients) - 1)] if clients else None
        attempt_options = ydl_options_for_player_client(options, client)
        try:
            logger.debug(
                "yt-dlp phase=metadata attempt=%s/%s client=%s",
                attempt,
                attempts,
                client or "auto",
            )
            with yt_dlp.YoutubeDL(attempt_options) as ydl:
                return ydl.extract_info(query, download=download)
        except Exception as error:
            last_error = error
            next_client = (
                clients[min(attempt, len(clients) - 1)]
                if clients and attempt < attempts
                else None
            )
            switching_client = bool(next_client and next_client != client)
            if (
                attempt >= attempts
                or (not switching_client and not is_transient_ytdlp_error(error))
            ):
                raise
            logger.warning(
                "yt-dlp phase=metadata lỗi (lần %s/%s, client=%s): %s "
                "— thử lại client=%s sau %.1fs",
                attempt,
                attempts,
                client or "auto",
                error,
                next_client or client or "auto",
                retry_delay,
            )
            time.sleep(max(0.0, float(retry_delay)))

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
