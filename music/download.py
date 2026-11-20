"""Shared file and retry rules for downloading music with yt-dlp."""

import glob
import os

from ytdlp_support import (
    is_transient_ytdlp_error,
    youtube_player_clients,
    youtube_ydl_routes,
)


AUDIO_CACHE_DIR = "audio_cache"


class AudioDownloadError(RuntimeError):
    """Lỗi chuẩn bị file âm thanh để gửi cho người dùng."""


def is_valid_file(path):
    return os.path.exists(path) and os.path.getsize(path) > 0


def youtube_download_attempt_specs(base_options, url):
    """Return client attempts for WARP first, then direct VPS egress."""
    clients = youtube_player_clients()
    if clients:
        attempt_clients = clients[:2]
        if len(attempt_clients) == 1:
            attempt_clients = (attempt_clients[0], attempt_clients[0])
    else:
        # Preserve the old home-PC behavior: one automatic-client attempt.
        attempt_clients = (None,)

    return tuple(
        (route_name, route_options, client)
        for route_name, route_options in youtube_ydl_routes(
            base_options,
            query=url,
        )
        for client in attempt_clients
    )


def can_continue_download_attempt(error, current_spec, next_spec):
    current_route, _, current_client = current_spec
    next_route, _, next_client = next_spec
    if next_route != current_route:
        return is_transient_ytdlp_error(error)
    return next_client != current_client or is_transient_ytdlp_error(error)


def remove_download_bundle(prefix):
    for path in glob.glob(f"{glob.escape(prefix)}*"):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
