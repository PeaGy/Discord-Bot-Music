from __future__ import annotations

import os
from pathlib import Path


def youtube_proxy_enabled() -> bool:
    """Return whether this deployment routes yt-dlp through a dedicated proxy."""
    return bool(os.getenv("YTDLP_PROXY", "").strip())


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


def youtube_ydl_options(base: dict | None = None) -> dict:
    """Apply optional YouTube proxy and JS settings for VPS deployments."""
    options = dict(base or {})

    proxy = os.getenv("YTDLP_PROXY", "").strip()
    runtime = os.getenv("YTDLP_JS_RUNTIME", "").strip().lower()
    bgutil_url = os.getenv("YTDLP_BGUTIL_URL", "").strip()
    components = {
        item.strip()
        for item in os.getenv("YTDLP_REMOTE_COMPONENTS", "").split(",")
        if item.strip()
    }

    # Preserve the old home-PC route unless this deployment explicitly opts in.
    if not proxy and not runtime and not components and not bgutil_url:
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
    if bgutil_url:
        # The native provider may listen on IPv6 only on some Linux hosts.
        # Point yt-dlp's HTTP provider at the reachable loopback address while
        # leaving the on-demand Node script available as its fallback.
        options["extractor_args"] = {
            "youtubepot-bgutilhttp": {"base_url": [bgutil_url]},
        }

    cookie_path = os.getenv("YTDLP_COOKIE_FILE", "cookies.txt").strip()
    if cookie_path and Path(cookie_path).is_file():
        options["cookiefile"] = cookie_path
    else:
        options.pop("cookiefile", None)

    return options
