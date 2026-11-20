"""Compare Node and Deno for Peto's direct-link metadata lookup on this host."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import logging
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from music.urls import canonical_youtube_url


def trial(url, runtime):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    os.environ["YTDLP_JS_RUNTIME"] = runtime
    import yt_dlp
    from yt_dlp.extractor.youtube.jsc._builtin.deno import DenoJCP
    from yt_dlp.extractor.youtube.jsc._builtin.node import NodeJCP
    from music.resolver import get_song_info

    provider = {"deno": DenoJCP, "node": NodeJCP}[runtime]
    run_js = provider._run_js_runtime
    samples = []
    audio_available = False
    sessions = []

    def timed_js(*args, **kwargs):
        start = perf_counter()
        result = run_js(*args, **kwargs)
        samples.append(perf_counter() - start)
        return result

    class Session(yt_dlp.YoutubeDL):
        def __init__(self, options):
            sessions.append("proxy" if options.get("proxy") else "default/direct")
            super().__init__(options)

        def extract_info(self, *args, **kwargs):
            nonlocal audio_available
            info = super().extract_info(*args, **kwargs)
            audio_available = any(f.get("url") and f.get("acodec") not in (None, "", "none")
                                  for f in (info or {}).get("formats") or [])
            return info

    with patch.object(provider, "_run_js_runtime", timed_js), patch.object(yt_dlp, "YoutubeDL", Session):
        start = perf_counter()
        song = get_song_info(url)
        elapsed = perf_counter() - start
    if len(sessions) != 1:
        return {"skip": "cache hit" if not sessions else "retry"}
    if not samples:
        return {"skip": "runtime did not solve a challenge"}
    if not audio_available or song.get("youtube_metadata_failed"):
        return {"skip": "no audio format"}
    return {"total": elapsed, "js": sum(samples), "route": sessions[0]}


def compare(url):
    for runtime in ("node", "deno"):
        if not shutil.which(runtime):
            print(f"Thiếu {runtime} trong PATH. Chưa chạy A/B.")
            return 1
    results = {"node": [], "deno": []}
    route = None
    for runtime in ("node", "deno") * 3:
        try:
            proc = subprocess.run([sys.executable, __file__, url, "--trial", runtime],
                                  capture_output=True, text=True, timeout=120)
            if proc.returncode:
                print(f"{runtime}: lượt đo không hợp lệ ({proc.stdout.strip() or 'worker error'}).")
                return 1
            row = json.loads(proc.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            print(f"{runtime}: {type(error).__name__}")
            return 1
        if "skip" in row:
            print(f"{runtime}: {row['skip']}. Chưa đủ dữ liệu so sánh.")
            return 1
        if route is not None and row["route"] != route:
            print("Tuyến mạng thay đổi giữa các lượt. Dừng so sánh.")
            return 1
        route = row["route"]
        results[runtime].append(row)
        print(f"{runtime}  JS={row['js']:.3f}s  metadata={row['total']:.3f}s  route={route}", flush=True)
    medians = {runtime: statistics.median(r["total"] for r in rows) for runtime, rows in results.items()}
    print(f"Trung vị metadata: Node={medians['node']:.3f}s  Deno={medians['deno']:.3f}s")
    print(f"Node trừ Deno: {medians['node'] - medians['deno']:+.3f}s (dương: Deno nhanh hơn)")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--trial", choices=("node", "deno"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    url = canonical_youtube_url(args.url)
    if not url:
        parser.error("Cần link video YouTube.")
    if not args.trial:
        sys.stdout.reconfigure(errors="replace")
        return compare(url)
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            row = trial(url, args.trial)
    except Exception as error:
        print(type(error).__name__)
        return 1
    print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
