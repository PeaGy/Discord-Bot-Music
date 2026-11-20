"""Time the direct-link /play lookup in a separate process on the bot host."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import logging
import os
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from music.urls import canonical_youtube_url


def request_kind(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    path = parsed.path
    if host in {"127.0.0.1", "localhost", "::1"}:
        return "local-provider"
    if host == "youtube.com" or host.endswith(".youtube.com"):
        if path.startswith("/youtubei/v1/player"):
            return "player-api"
        if path.endswith(".js"):
            return "player-js"
        if path == "/watch":
            return "watch-page"
        return "youtube-other"
    return "other"


def profile(url, lookup, ydl_class):
    events = []
    started = perf_counter()

    class TimedYoutubeDL(ydl_class):
        def __init__(self, options):
            begin = perf_counter()
            try:
                super().__init__(options)
            finally:
                route = "configured-proxy" if options.get("proxy") else "default/direct"
                events.append((begin, perf_counter(), "session-init " + route))

        def urlopen(self, request):
            address = request if isinstance(request, str) else getattr(request, "url", None)
            if address is None:
                address = request.get_full_url()
            kind = request_kind(address)
            begin = perf_counter()
            try:
                response = super().urlopen(request)
            finally:
                events.append((begin, perf_counter(), kind + " open"))
            original_read = response.read

            def timed_read(*args, **kwargs):
                begin = perf_counter()
                try:
                    return original_read(*args, **kwargs)
                finally:
                    events.append((begin, perf_counter(), kind + " read"))

            response.read = timed_read
            return response

    status = "ok"
    try:
        with patch("yt_dlp.YoutubeDL", TimedYoutubeDL):
            song = lookup(url)
            if song.get("youtube_metadata_failed"):
                status = "metadata-only"
    except Exception as error:
        status = type(error).__name__
    end = perf_counter()
    return status, events, started, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Link video YouTube")
    url = canonical_youtube_url(parser.parse_args().url)
    if not url:
        parser.error("Cần link tới một video YouTube.")
    # Suppress raw yt-dlp warnings, query strings and configured endpoints.
    logging.disable(logging.CRITICAL)
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        from commands.play import get_song_info
        from yt_dlp import YoutubeDL

        status, events, start, end = profile(url, get_song_info, YoutubeDL)
    if status == "ok" and not events:
        print("Cache hit. Chọn một link chưa có cache để đo YouTube.")
        return 0
    print("start(s)  elapsed(s)  phase")
    covered_until = start
    other = 0.0
    for begin, finish, name in sorted(events):
        other += max(0.0, begin - covered_until)
        covered_until = max(covered_until, finish)
        print(f"{begin-start:8.3f}  {finish-begin:10.3f}  {name}")
    print(f"unattributed={other + end - covered_until:.3f}s total={end-start:.3f}s result={status}")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
