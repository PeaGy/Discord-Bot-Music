"""Opt-in FFmpeg/HLS smoke check. Loopback only; no bot login or music cache."""
from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from music.stream_source import open_fallback_stream


async def check() -> None:
    # Generate a one-second test tone in RAM, not a cached music file.
    segment = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=1", "-c:a", "aac", "-f", "mpegts", "pipe:1"],
        capture_output=True, check=True, timeout=10,
    ).stdout
    playlist = (
        b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
        b"#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1.0,\npart.ts\n#EXT-X-ENDLIST\n"
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("User-Agent") != "PetoStreamCheck/1.0":
                self.send_error(403)
                return
            if self.path == "/stream.m3u8":
                payload, content_type = playlist, "application/vnd.apple.mpegurl"
            elif self.path == "/part.ts":
                payload, content_type = segment, "video/mp2t"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = None
    try:
        source = await open_fallback_stream({
            "stream_url": f"http://127.0.0.1:{server.server_port}/stream.m3u8",
            "http_headers": {"User-Agent": "PetoStreamCheck/1.0"},
        })

        def drain():
            frames = 0
            while source.read():
                frames += 1
            return frames

        frames = await asyncio.wait_for(asyncio.to_thread(drain), timeout=5)
        assert frames >= 45, f"Missing audio: only {frames} PCM frames"
        print(f"OK: HLS read {frames} audio frames; headers forwarded; EOF completed.")
    finally:
        if source:
            await asyncio.to_thread(source.cleanup)
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    asyncio.run(check())
