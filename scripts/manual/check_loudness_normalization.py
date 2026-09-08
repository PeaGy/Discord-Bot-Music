"""Opt-in real FFmpeg check using only generated audio in a temporary folder."""

from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cache_manager import measure_loudness_sync, normalize_and_encode_sync


def check():
    with tempfile.TemporaryDirectory(prefix="peto-loudness-") as directory:
        raw = Path(directory) / "synthetic.wav"
        final = Path(directory) / "normalized.opus"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i",
             "sine=frequency=440:sample_rate=48000:duration=8", "-ac", "2",
             "-c:a", "pcm_s16le", str(raw)],
            capture_output=True, check=True, timeout=20,
        )
        stats = measure_loudness_sync(str(raw))
        assert stats is not None, "No valid measurement from real FFmpeg"
        normalize_and_encode_sync(str(raw), str(final), stats)
        assert final.is_file() and final.stat().st_size > 0, "No encoded audio"
        result = measure_loudness_sync(str(final))
        assert result is not None, "Unable to decode/measure the encoded Opus"
        loudness = float(result["input_i"])
        assert abs(loudness - (-16.0)) < 1.0, f"Unexpected fixture loudness: {loudness}"
        print(f"OK: real FFmpeg measurement + Opus encode; output {loudness:.2f} LUFS.")
        print("Synthetic files cleaned on exit; no network or existing audio cache used.")


if __name__ == "__main__":
    check()
