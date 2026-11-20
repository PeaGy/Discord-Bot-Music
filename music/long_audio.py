"""Download and play temporary local audio for long YouTube tracks."""

import asyncio
import glob
import logging
import os
import time
import uuid

import discord
import yt_dlp

from music.audio_source import OpusPrerollAudioSource
from music.download import (
    AUDIO_CACHE_DIR,
    AudioDownloadError,
    can_continue_download_attempt,
    is_valid_file,
    remove_download_bundle,
    youtube_download_attempt_specs,
)
from ytdlp_support import ydl_options_for_player_client, youtube_ydl_options, youtube_ydl_routes


logger = logging.getLogger(__name__)

LONG_AUDIO_TEMP_DIR = os.path.join(AUDIO_CACHE_DIR, "long_temp")
os.makedirs(LONG_AUDIO_TEMP_DIR, exist_ok=True)

LONG_AUDIO_TEMP_MAX_DURATION = 2 * 60 * 60
LONG_AUDIO_TEMP_MAX_BYTES = 300 * 1024 * 1024
LONG_AUDIO_TEMP_STALE_SECONDS = 6 * 60 * 60

_long_audio_download_semaphore = asyncio.Semaphore(2)


class TemporaryFileAudioSource(discord.AudioSource):
    """Audio source that removes its downloaded file when Discord is done with it."""

    def __init__(self, source, filepath):
        self.source = source
        self.filepath = filepath
        self._cleaned = False

    def read(self):
        return self.source.read()

    def is_opus(self):
        return self.source.is_opus()

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        try:
            self.source.cleanup()
        finally:
            try:
                os.remove(self.filepath)
                logger.info("Đã xóa audio tạm của bài dài: %s", self.filepath)
            except FileNotFoundError:
                pass
            except OSError as error:
                logger.warning("Không xóa được audio tạm %s: %s", self.filepath, error)


def cleanup_stale_long_audio_files(max_age=LONG_AUDIO_TEMP_STALE_SECONDS):
    """Remove abandoned long-track files left behind by a crash or forced shutdown."""
    cutoff = time.time() - max(0, int(max_age))
    try:
        entries = os.scandir(LONG_AUDIO_TEMP_DIR)
    except OSError as error:
        logger.warning("Không quét được thư mục audio tạm: %s", error)
        return

    with entries:
        for entry in entries:
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
            except OSError:
                continue


def _download_long_audio_sync(url, player_client=None, route_name=None):
    """Download original long-form audio through yt-dlp without normalization."""
    token = uuid.uuid4().hex
    prefix = os.path.join(LONG_AUDIO_TEMP_DIR, f"long_{token}_")
    outtmpl = f"{prefix}%(id)s.%(ext)s"
    ydl_opts = youtube_ydl_options({
        "format": "bestaudio[acodec^=opus]/bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "continuedl": True,
        "retries": 2,
        "fragment_retries": 2,
        "max_filesize": LONG_AUDIO_TEMP_MAX_BYTES,
    })
    available_routes = dict(youtube_ydl_routes(ydl_opts, query=url))
    if route_name in available_routes:
        ydl_opts = available_routes[route_name]
    ydl_opts = ydl_options_for_player_client(ydl_opts, player_client)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if "entries" in info:
                info = next((entry for entry in info["entries"] if entry), None)
            if not info:
                raise AudioDownloadError("yt-dlp không trả về thông tin audio.")

            filepath = ydl.prepare_filename(info)
            if not is_valid_file(filepath):
                candidates = [
                    path for path in glob.glob(f"{glob.escape(prefix)}*")
                    if is_valid_file(path) and not path.endswith(".part")
                ]
                if not candidates:
                    raise AudioDownloadError("Không tìm thấy file audio dài sau khi tải.")
                filepath = max(candidates, key=os.path.getmtime)

            if os.path.getsize(filepath) > LONG_AUDIO_TEMP_MAX_BYTES:
                raise AudioDownloadError("Audio dài vượt quá giới hạn 300 MiB.")

            requested = info.get("requested_downloads") or []
            selected = requested[0] if requested else info
            codec = str(selected.get("acodec") or info.get("acodec") or "").lower()
            return filepath, codec
    except Exception:
        remove_download_bundle(prefix)
        raise


async def get_long_audio_source(url, duration):
    """Prepare a temporary local source for a proxied long track, with one retry."""
    duration = int(duration or 0)
    if duration > LONG_AUDIO_TEMP_MAX_DURATION:
        raise AudioDownloadError(
            "Bài dài vượt quá giới hạn phát tạm 2 giờ trên VPS."
        )

    cleanup_stale_long_audio_files()
    base_options = youtube_ydl_options({})
    attempt_specs = youtube_download_attempt_specs(base_options, url)
    last_error = None
    total_attempts = len(attempt_specs)
    for attempt, spec in enumerate(attempt_specs, start=1):
        route_name, _, player_client = spec
        filepath = None
        try:
            logger.info(
                "Tải audio tạm cho bài dài "
                "(lần %s/%s, route=%s, client=%s): %s",
                attempt,
                total_attempts,
                route_name,
                player_client or "auto",
                url,
            )
            async with _long_audio_download_semaphore:
                filepath, codec = await asyncio.to_thread(
                    _download_long_audio_sync,
                    url,
                    player_client,
                    route_name,
                )
            ffmpeg_source = discord.FFmpegOpusAudio(
                filepath,
                codec="copy" if "opus" in codec else "libopus",
                bitrate=160,
                options="-vn",
            )
            logger.info(
                "Audio tạm bài dài sẵn sàng: %s (%s)",
                filepath,
                codec or "codec không rõ",
            )
            return TemporaryFileAudioSource(
                OpusPrerollAudioSource(ffmpeg_source),
                filepath,
            )
        except Exception as error:
            last_error = error
            if filepath:
                try:
                    os.remove(filepath)
                except OSError:
                    pass
            logger.warning(
                "Không chuẩn bị được audio tạm bài dài "
                "(lần %s/%s, route=%s, client=%s): %s",
                attempt,
                total_attempts,
                route_name,
                player_client or "auto",
                error,
            )
            if attempt < total_attempts and can_continue_download_attempt(
                error,
                spec,
                attempt_specs[attempt],
            ):
                await asyncio.sleep(3)
                continue
            break

    raise AudioDownloadError("Không tải được audio tạm cho bài dài.") from last_error
