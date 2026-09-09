"""Offline tests: no FFmpeg, network, production cache or settings required."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from music.loudness import measurement_command, normalization_command, opus_encode_options
from scripts.manual import benchmark_audio_pipeline as bench


STATS = {"input_i": "-20", "input_tp": "-6", "input_lra": "5", "input_thresh": "-30", "target_offset": "0"}


class BenchmarkTests(unittest.TestCase):
    def test_import_is_standalone_without_bot_cache_or_dependencies(self):
        root = str(Path(__file__).resolve().parents[1])
        code = (
            f"import sys; sys.path.insert(0, {root!r}); "
            "from scripts.manual import benchmark_audio_pipeline; "
            "assert not {'cache_manager', 'discord', 'yt_dlp', 'config'} & set(sys.modules)"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", "-c", code], cwd=directory, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_shared_commands_keep_production_target_and_codec_options(self):
        self.assertEqual(measurement_command("raw.opus"), [
            "ffmpeg", "-i", "raw.opus", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-",
        ])
        expected = ["-c:a", "libopus", "-b:a", "160k", "-application", "audio", "-frame_duration", "20", "-packet_loss", "10", "-fec", "1"]
        self.assertEqual(opus_encode_options(), expected)
        changed = opus_encode_options()
        changed.append("unused")
        self.assertEqual(opus_encode_options(), expected)
        for stats in (None, STATS):
            command = normalization_command("raw.opus", "out.opus", stats)
            self.assertEqual(command[-len(expected) - 1:-1], expected)
            self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11", command[command.index("-af") + 1])
            if stats:
                self.assertIn("linear=true", command[command.index("-af") + 1])

    def test_variants_only_add_filter_threads_to_unchanged_command(self):
        original = normalization_command("raw.opus", "out.opus", STATS)
        for variant in bench.VARIANTS:
            command = bench.instrument(original, "ffmpeg-custom", variant)
            extra = [] if variant == "default" else ["-filter_threads", variant[-1]]
            self.assertEqual(command, ["ffmpeg-custom", "-hide_banner", "-nostdin", "-nostats", "-benchmark", *extra, *original[1:]])
            self.assertNotIn("-threads", command)
            self.assertNotIn("-ar", command)
        with self.assertRaises(ValueError):
            bench.instrument(original, "ffmpeg", "unknown")

    def test_benchmark_parsing_and_missing_metrics(self):
        self.assertEqual(bench.parse_benchmark("log\nbench: utime=1.250s stime=0.250s rtime=0.900s\n"), (1.25, 0.25, 0.9))
        for text in ("", "bench: utime=NaNs stime=1s rtime=2s", "bench: utime=-1s stime=1s rtime=2s"):
            self.assertIsNone(bench.parse_benchmark(text))

    def test_runner_records_wall_and_sanitizes_environment(self):
        result = SimpleNamespace(returncode=0, stdout="ok", stderr="bench: utime=1.0s stime=0.5s rtime=0.8s")
        with patch.dict(os.environ, {"FFREPORT": "file=PRIVATE.log"}), patch.object(
            bench.subprocess, "run", return_value=result,
        ) as run, patch.object(bench.time, "perf_counter", side_effect=[10.0, 11.2]):
            measured = bench.run_command(["ffmpeg", "-version"])
        self.assertAlmostEqual(measured.wall, 1.2)
        self.assertEqual((measured.user, measured.system, measured.real), (1, 0.5, 0.8))
        self.assertNotIn("FFREPORT", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["timeout"], bench.TIMEOUT)

    def test_subprocess_failures_are_safe_and_never_success(self):
        errors = [FileNotFoundError("PRIVATE"), subprocess.TimeoutExpired(["PRIVATE"], 180)]
        for error in errors:
            with self.subTest(error=error), patch.object(bench.subprocess, "run", side_effect=error):
                with self.assertRaises(bench.BenchmarkError) as raised:
                    bench.run_command(["ffmpeg"])
                self.assertNotIn("PRIVATE", str(raised.exception))
        with patch.object(bench.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="PRIVATE", stderr="PRIVATE")):
            with self.assertRaises(bench.BenchmarkError) as raised:
                bench.run_command(["ffmpeg"])
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_output_probe_validates_codec_channels_rate_and_duration(self):
        valid = {"streams": [{"codec_type": "audio", "codec_name": "opus", "sample_rate": "48000", "channels": 2}], "format": {"duration": "120.0065"}}
        result = bench.Result(1, json.dumps(valid), "")
        with patch.object(bench, "run_command", return_value=result):
            self.assertAlmostEqual(bench.probe_output("ffprobe", "temp.opus", 120), 120.0065)
        invalid = [None, {}, {**valid, "format": {"duration": "nan"}}, {**valid, "format": {"duration": "119"}}]
        for field, value in (("codec_name", "aac"), ("sample_rate", "44100"), ("channels", 1)):
            invalid.append({**valid, "streams": [{**valid["streams"][0], field: value}]})
        for info in invalid:
            with self.subTest(info=info), patch.object(bench, "run_command", return_value=bench.Result(1, json.dumps(info), "")):
                with self.assertRaises(bench.BenchmarkError):
                    bench.probe_output("ffprobe", "temp.opus", 120)

    def test_loudness_and_mode_guards_reject_invalid_outputs(self):
        self.assertEqual(bench.validate_loudness({**STATS, "input_i": "-16.04"}), (-16.04, -6))
        for stats in (None, {}, {**STATS, "input_i": "nan"}, {**STATS, "input_i": "-18"}, {**STATS, "input_i": "-16", "input_tp": "-0.5"}):
            with self.subTest(stats=stats), self.assertRaises(bench.BenchmarkError):
                bench.validate_loudness(stats)
        self.assertEqual(bench.normalization_mode("Normalization Type: Linear\n"), "linear")
        with self.assertRaises(bench.BenchmarkError):
            bench.normalization_mode("missing")
        with self.assertRaises(bench.BenchmarkError):
            bench.check_consistency({"default": [("linear", -16, -2)], "filter1": [("dynamic", -16, -2)]})

    def test_rotating_order_and_summary_use_paired_totals(self):
        self.assertEqual([bench.variant_order(i)[0] for i in range(3)], list(bench.VARIANTS))
        records = {(stage, variant): [bench.Result(1, "", "")] * 3 for variant in bench.VARIANTS for stage in ("measure", "normalize+encode")}
        records.update({("decode", "default"): [bench.Result(1, "", "")] * 3, ("decode+encode", "default"): [bench.Result(1, "", "")] * 3})
        records[("measure", "default")] = [bench.Result(n, "", "") for n in (1, 2, 100)]
        records[("normalize+encode", "default")] = [bench.Result(n, "", "") for n in (100, 2, 1)]
        quality = {variant: [("linear", -16.04, -2)] * 3 for variant in bench.VARIANTS}
        output = io.StringIO()
        with redirect_stdout(output):
            bench.print_summary(records, quality, 3)
        self.assertIn("default  101.000 [4.000..101.000]", output.getvalue())
        self.assertIn("N/A", output.getvalue())

    def test_temporary_fixture_removed_on_failure_or_keyboard_interrupt(self):
        for error in (bench.BenchmarkError("failed"), KeyboardInterrupt()):
            created = []

            def fail(command, **kwargs):
                path = Path(command[-1])
                path.write_bytes(b"synthetic only")
                created.append(path)
                raise error

            with self.subTest(error=type(error).__name__), patch.object(bench, "run_command", side_effect=fail), redirect_stdout(io.StringIO()):
                with self.assertRaises(type(error)):
                    bench.benchmark("ffmpeg", "ffprobe", 10, 1)
            self.assertEqual(len(created), 1)
            self.assertFalse(created[0].parent.exists())

    def test_cli_bounds_and_missing_binaries(self):
        self.assertEqual(bench.bounded_int(10, 300)("120"), 120)
        for value in ("0", "301", "not a number"):
            with self.assertRaises(bench.argparse.ArgumentTypeError):
                bench.bounded_int(10, 300)(value)
        with patch.object(bench.shutil, "which", return_value=None), redirect_stdout(io.StringIO()):
            self.assertEqual(bench.main([]), 1)


if __name__ == "__main__":
    unittest.main()
