"""Finite fallback streams: bounded startup, no audio files, no EOF reconnect."""
from __future__ import annotations

import asyncio
import shlex
import threading

import discord


STREAM_STARTUP_TIMEOUT = 8.0
_PCM_FRAME_BYTES = 3840  # Discord: 20 ms, 48 kHz, stereo signed 16-bit PCM.
_ALLOWED_HEADERS = {"user-agent", "referer", "origin", "accept", "accept-language"}


def finite_stream_options(headers: dict | None = None) -> dict:
    before = (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 2 "
        "-reconnect_on_network_error 1 -rw_timeout 8000000"
    )
    safe_headers = []
    for name, value in (headers or {}).items():
        name, value = str(name), str(value)
        if name.casefold() not in _ALLOWED_HEADERS:
            continue
        if any(character in name + value for character in ("\r", "\n", "\0")):
            continue
        if len(value) <= 4096:
            safe_headers.append(f"{name}: {value}\r\n")
    if safe_headers:
        # discord.py splits these options with shlex on both Windows and Linux;
        # Popen uses shell=False. Never log headers or signed media URLs.
        before += " -headers " + shlex.quote("".join(safe_headers))
    return {"before_options": before, "options": "-vn"}


class _PrimedPCM(discord.AudioSource):
    """Replay the frame read during startup; do not lose the start of the song."""

    def __init__(self, source: discord.AudioSource, first_frame: bytes, release):
        self._source = source
        self._first_frame = first_frame
        self._release = release

    def read(self) -> bytes:
        if self._first_frame:
            frame, self._first_frame = self._first_frame, b""
            return frame
        return self._source.read() if self._source else b""

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._source = None
        self._first_frame = b""
        release, self._release = getattr(self, "_release", None), None
        if release:
            release()


class _StreamStartup:
    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._source = None

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            source, self._source = self._source, None
        if source:
            source.cleanup()  # Kill FFmpeg to unblock the pending pipe read.

    def open(self, info: dict) -> discord.AudioSource:
        source = discord.FFmpegPCMAudio(
            info["stream_url"], **finite_stream_options(info.get("http_headers"))
        )
        with self._lock:
            cancelled = self._cancelled
            if not cancelled:
                self._source = source
        if cancelled:
            source.cleanup()
            raise RuntimeError("Đã hủy khởi động stream")
        try:
            frame = source.read()
            if len(frame) != _PCM_FRAME_BYTES:
                raise RuntimeError("Stream kết thúc trước khi có frame audio đầu tiên")
            # Both timeout cleanup and normal playback cleanup share the same
            # lock-protected release, so a completion/cancellation race cannot
            # clean the underlying FFmpeg process twice.
            return _PrimedPCM(source, frame, self.cancel)
        except BaseException:
            self.cancel()
            raise


def _consume_finished(future: asyncio.Future) -> None:
    # Timeout/cancellation must not leave an unobserved executor exception.
    if not future.cancelled():
        future.exception()


async def open_fallback_stream(
    info: dict, *, timeout: float = STREAM_STARTUP_TIMEOUT
) -> discord.AudioSource:
    startup = _StreamStartup()
    future = asyncio.get_running_loop().run_in_executor(None, startup.open, info)
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
    except BaseException:
        future.add_done_callback(_consume_finished)
        # Cleanup may wait for the subprocess: keep that work off the event loop.
        await asyncio.shield(asyncio.to_thread(startup.cancel))
        raise
