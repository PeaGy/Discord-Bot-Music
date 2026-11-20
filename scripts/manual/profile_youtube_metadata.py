"""Time the direct-link /play lookup in a separate process on the bot host."""

import argparse
import cProfile
from contextlib import redirect_stderr, redirect_stdout
import logging
import os
import pstats
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


class PlayerTail:
    """Keep only work on the lookup thread after its latest player response."""

    def __init__(self):
        self.profiler = None
        self.start = None

    def restart(self):
        self.stop()
        self.profiler = cProfile.Profile()
        self.start = perf_counter()
        self.profiler.enable()

    def stop(self):
        if self.profiler is not None:
            self.profiler.disable()

    def report(self, end):
        if self.profiler is None:
            print("No player-api response observed.")
            return
        print(f"player-api tail={end-self.start:.3f}s (main thread; inclusive rows overlap)")
        rows = []
        for (file, line, name), (_, calls, own, total, _) in pstats.Stats(self.profiler).stats.items():
            parts = Path(file).parts
            for root in ("yt_dlp", "yt_dlp_plugins"):
                if root in parts:
                    label = "/".join(parts[parts.index(root):])
                    break
            else:
                if Path(file).name != "subprocess.py":
                    continue
                label = "subprocess.py"
            rows.append((total, own, calls, f"{label}:{line} {name}"))
        # Include the slowest functions within each suspected subsystem even if
        # generic extractor wrappers occupy the top cumulative positions.
        rows.sort(reverse=True)
        suspects = [next((row for row in rows if marker in row[3]), None)
                    for marker in ("/jsc/", "/pot/", "yt_dlp_plugins/", "_run_js_runtime", "subprocess.py:")]
        print("total(s)   self(s)  calls  function")
        for total, own, calls, label in dict.fromkeys(rows[:12] + [r for r in suspects if r]):
            print(f"{total:8.3f}  {own:8.3f}  {calls:5}  {label}")


def profile(url, lookup, ydl_class, tail=None):
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
            if tail is not None and kind == "player-api":
                tail.restart()

                def player_read(*args, **kwargs):
                    data = timed_read(*args, **kwargs)
                    tail.restart()
                    return data

                response.read = player_read
            return response

    status = "ok"
    try:
        with patch("yt_dlp.YoutubeDL", TimedYoutubeDL):
            song = lookup(url)
            if song.get("youtube_metadata_failed"):
                status = "metadata-only"
    except Exception as error:
        status = type(error).__name__
    finally:
        if tail is not None:
            tail.stop()
    end = perf_counter()
    return status, events, started, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Link video YouTube")
    parser.add_argument("--detail", action="store_true", help="Profile Python calls after the final player API response")
    args = parser.parse_args()
    tail = PlayerTail() if args.detail else None
    url = canonical_youtube_url(args.url)
    if not url:
        parser.error("Cần link tới một video YouTube.")
    # Suppress raw yt-dlp warnings, query strings and configured endpoints.
    logging.disable(logging.CRITICAL)
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        from music.resolver import get_song_info
        from yt_dlp import YoutubeDL

        status, events, start, end = profile(url, get_song_info, YoutubeDL, tail)
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
    if tail is not None:
        tail.report(end)
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
