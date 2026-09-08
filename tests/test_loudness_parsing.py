import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cache_manager


STATS = {
    "input_i": "-19.25",
    "input_tp": "-1.8",
    "input_lra": "3.1",
    "input_thresh": "-29.7",
    "target_offset": "0.15",
}
SECRET = "private-path-proxy-password-and-token"


def messages(logger):
    return [
        call.args[0] % call.args[1:]
        for method in (logger.info, logger.warning, logger.debug, logger.error)
        for call in method.call_args_list
        if call.args
    ]


class LoudnessStatsParsingTests(unittest.TestCase):
    def test_real_style_stats_accept_trailing_ffmpeg_summary(self):
        stderr = (
            "Input #0, matroska,webm, from 'raw.webm':\n"
            "[Parsed_loudnorm_0 @ 0x1234]\n"
            + json.dumps({**STATS, "output_i": "-16.0", "normalization_type": "dynamic"}, indent=4)
            + "\n[out#0/null @ 0x4567] video:0KiB audio:48123KiB\n"
            "size=N/A time=00:03:58.00 bitrate=N/A speed=120x\n"
        )
        self.assertEqual(cache_manager._parse_loudness_stats(stderr), STATS)

    def test_unrelated_braces_invalid_and_unrelated_json_do_not_hide_valid_stats(self):
        stderr = (
            "opening {not JSON; nested { FFmpeg warning\n"
            + json.dumps({"other": {"nested": "prefix"}})
            + "\n" + json.dumps(STATS)
            + "\n" + json.dumps({"status": "done", "metadata": SECRET})
            + "\nfinal unmatched {warning}\n"
        )
        self.assertEqual(cache_manager._parse_loudness_stats(stderr), STATS)

    def test_multiple_or_truncated_loudness_reports_are_ambiguous(self):
        valid = json.dumps(STATS)
        incomplete = json.dumps({"input_i": "-17.2"})
        truncated = '{"input_i": "-17.2", "input_tp":'
        for stderr in (valid + "\n" + valid, valid + "\n" + incomplete,
                       incomplete + "\n" + valid, valid + "\n" + truncated,
                       truncated + "\n" + valid):
            with self.subTest(stderr=stderr):
                self.assertIsNone(cache_manager._parse_loudness_stats(stderr))

    def test_numbers_are_normalized_and_extraneous_fields_are_dropped(self):
        stats = {"input_i": -19.25000, "input_tp": "-1.8000", "input_lra": 3.100,
                 "input_thresh": "-2.97e1", "target_offset": "1.500e-1", "private": SECRET}
        parsed = cache_manager._parse_loudness_stats(json.dumps(stats))
        self.assertEqual(parsed, STATS)
        self.assertEqual(set(parsed), set(STATS))
        self.assertTrue(all(isinstance(value, str) for value in parsed.values()))

    def test_every_measurement_field_is_required(self):
        for key in STATS:
            with self.subTest(key=key):
                partial = {field: value for field, value in STATS.items() if field != key}
                self.assertIsNone(cache_manager._parse_loudness_stats(json.dumps(partial)))

    def test_booleans_non_numeric_and_non_finite_stats_are_rejected(self):
        for value in (True, False, None, {}, [], "NaN", "inf", "-inf", "Infinity",
                      float("nan"), float("inf"), float("-inf"), "1e999", "-19:extra_filter"):
            with self.subTest(value=value):
                self.assertIsNone(cache_manager._parse_loudness_stats(json.dumps({**STATS, "input_i": value})))
        silent = {**STATS, "input_i": "-inf", "input_tp": "-inf", "target_offset": "inf"}
        self.assertIsNone(cache_manager._parse_loudness_stats(json.dumps(silent)))

    def test_absent_malformed_and_non_object_stats_are_rejected(self):
        for stderr in ("", "ffmpeg: error", "{invalid}", "[]", "null", "123", '{"input_i":'):
            with self.subTest(stderr=stderr):
                self.assertIsNone(cache_manager._parse_loudness_stats(stderr))


class LoudnessMeasurementTests(unittest.TestCase):
    def test_successful_process_accepts_json_followed_by_summary(self):
        result = SimpleNamespace(returncode=0, stderr=json.dumps(STATS) + "\nsize=N/A speed=99x\n")
        with patch.object(cache_manager.subprocess, "run", return_value=result) as run:
            self.assertEqual(cache_manager.measure_loudness_sync("raw.webm"), STATS)
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        self.assertIn(f"loudnorm={cache_manager.LOUDNORM_TARGET}:print_format=json", run.call_args.args[0])

    def test_failed_process_cannot_supply_successful_measurement_even_with_valid_json(self):
        logger = Mock()
        result = SimpleNamespace(returncode=1, stderr=json.dumps(STATS) + SECRET)
        with patch.object(cache_manager, "logger", logger), patch.object(
            cache_manager.subprocess, "run", return_value=result
        ):
            self.assertIsNone(cache_manager.measure_loudness_sync(SECRET))
        self.assert_safe_reason(logger)

    def test_timeout_missing_ffmpeg_and_invalid_stats_only_log_safe_reason(self):
        for error in (subprocess.TimeoutExpired(["ffmpeg", SECRET], 120, stderr=SECRET),
                      FileNotFoundError(SECRET), OSError(SECRET)):
            with self.subTest(error=type(error).__name__):
                logger = Mock()
                with patch.object(cache_manager, "logger", logger), patch.object(
                    cache_manager.subprocess, "run", side_effect=error
                ):
                    self.assertIsNone(cache_manager.measure_loudness_sync(SECRET))
                self.assert_safe_reason(logger)
        logger = Mock()
        result = SimpleNamespace(returncode=0, stderr=f"{SECRET} {{invalid_json}}")
        with patch.object(cache_manager, "logger", logger), patch.object(
            cache_manager.subprocess, "run", return_value=result
        ):
            self.assertIsNone(cache_manager.measure_loudness_sync(SECRET))
        self.assert_safe_reason(logger)

    def assert_safe_reason(self, logger):
        output = "\n".join(messages(logger))
        self.assertIn("reason=", output)
        self.assertNotIn(SECRET, output)


class LoudnessBuildOutcomeTests(unittest.TestCase):
    def test_unavailable_measurement_keeps_normalization_but_is_not_logged_as_success(self):
        self.check_build(None, "fallback", "unavailable")

    def test_valid_measurement_logs_success_and_passes_stats_to_encoder(self):
        self.check_build(STATS, "success", "ok")

    def check_build(self, stats, outcome, measurement):
        with tempfile.TemporaryDirectory() as directory:
            raw_path = Path(directory) / "raw.webm"
            raw_path.write_bytes(b"mock raw")
            final_path = Path(directory) / "final.opus"
            logger = Mock()

            def encode(raw, staged, measured):
                self.assertEqual(raw, str(raw_path))
                self.assertIs(measured, stats)
                Path(staged).write_bytes(b"mock normalized opus")

            with patch.object(cache_manager, "logger", logger), patch.object(
                cache_manager, "download_raw_sync", return_value=str(raw_path)
            ), patch.object(cache_manager, "measure_loudness_sync", return_value=stats), patch.object(
                cache_manager, "normalize_and_encode_sync", side_effect=encode
            ) as normalize:
                cache_manager.build_cache_sync(
                    "https://www.youtube.com/watch?v=mRD0-GxqHVo", "unused", str(final_path),
                )
            normalize.assert_called_once()
            self.assertEqual(final_path.read_bytes(), b"mock normalized opus")
            self.assertFalse(raw_path.exists())
            self.assertEqual(list(Path(directory).iterdir()), [final_path])
        logs = messages(logger)
        measurement_phases = [message for message in logs if "phase=loudness-measure " in message]
        self.assertEqual(len(measurement_phases), 1)
        self.assertIn(f"outcome={outcome}", measurement_phases[0])
        self.assertTrue(any(f"measurement={measurement}" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
