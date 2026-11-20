"""Compare production cache normalization with measured loudnorm on synthetic Opus."""

from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cache_manager import measure_loudness_sync, normalize_and_encode_sync
from music.loudness import measurement_command, parse_loudness_stats


def reference_measure(path):
    result = subprocess.run(measurement_command(path), capture_output=True, text=True,
                            check=True, timeout=120)
    stats = parse_loudness_stats(result.stderr)
    assert stats is not None, "Missing reference loudness measurement"
    return stats


def check():
    fixtures = {
        "linear": r"0.08*sin(2*PI*440*t)*if(lt(mod(t\,12)\,6)\,1\,0.6)",
        "transient": r"0.018*sin(2*PI*440*t)+0.8*sin(2*PI*1000*t)*lt(mod(t\,3)\,0.008)",
    }
    with tempfile.TemporaryDirectory(prefix="peto-loudness-") as directory:
        raw = str(Path(directory) / "synthetic.opus")
        output = str(Path(directory) / "normalized.opus")
        for name, signal in fixtures.items():
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"aevalsrc={signal}:s=48000:d=36",
                            "-ac", "2", "-c:a", "libopus", raw], capture_output=True, check=True, timeout=60)
            results = []
            for measure in (reference_measure, measure_loudness_sync):
                start = time.perf_counter()
                stats = measure(raw)
                assert stats is not None
                if measure is measure_loudness_sync:
                    assert ("gain_db" in stats) == (name == "linear"), f"Unexpected route for {name}"
                normalize_and_encode_sync(raw, output, stats)
                elapsed = time.perf_counter() - start
                checked = reference_measure(output)
                loudness, peak = float(checked["input_i"]), float(checked["input_tp"])
                results.append((loudness, peak))
                print(f"{name} {measure.__name__}: {elapsed:.2f}s, {loudness:.2f} LUFS, {peak:.2f} dBTP", flush=True)
            baseline, current = results
            tolerance = 0.5 if name == "linear" else 0.1
            assert abs(current[0] - baseline[0]) <= tolerance, f"Loudness regression on {name}"
            assert current[1] <= -1.2, f"True peak regression on {name}"


if __name__ == "__main__":
    check()
