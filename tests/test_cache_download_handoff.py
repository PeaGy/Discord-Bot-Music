"""Offline coverage for fresh metadata downloads and unchanged audio quality."""

from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cache_manager
import yt_dlp


URL = "https://www.youtube.com/watch?v=H5v3kku4y6Q"


class DownloadHandoffTests(unittest.TestCase):
    def run_download(self, seed=None, fail_seed=False):
        calls, sessions = [], []

        class FakeYDL:
            def __init__(self, options):
                self.options = options
                sessions.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, url, *, download):
                calls.append(("extract", download))
                return {"id": "fresh"}

            def process_ie_result(self, info, *, download):
                calls.append(("download", download, info["id"]))
                if fail_seed and info["id"] == "seed":
                    raise RuntimeError("expired https://signed.test/?token=PRIVATE")
                return info

            def prepare_filename(self, _info):
                return "audio.webm"

        with ExitStack() as stack:
            stack.enter_context(patch.object(cache_manager, "youtube_ydl_options", side_effect=lambda options: options))
            stack.enter_context(patch.object(cache_manager, "_youtube_download_attempt_specs", return_value=(("direct", {}, None),)))
            stack.enter_context(patch.object(cache_manager, "take_youtube_download_seed", return_value=seed))
            stack.enter_context(patch.object(cache_manager.yt_dlp, "YoutubeDL", FakeYDL))
            cleanup = stack.enter_context(patch.object(cache_manager, "_remove_long_audio_bundle"))
            logs = stack.enter_context(self.assertLogs("cache_manager", level="INFO"))
            result = cache_manager.download_raw_sync(URL, "raw.%(ext)s")
        return result, calls, sessions, cleanup.call_count, "\n".join(logs.output)

    def seed(self):
        return SimpleNamespace(
            info={"id": "seed"}, options={"proxy": "http://user:PRIVATE@proxy.test"},
            route_name="proxy", player_client="mweb", cookiejar=object(),
        )

    def test_fresh_metadata_skips_extraction_and_keeps_session(self):
        seed = self.seed()
        result, calls, sessions, cleaned, logs = self.run_download(seed)
        self.assertEqual(result, "audio.webm")
        self.assertEqual(calls, [("download", True, "seed")])
        self.assertIs(sessions[0].cookiejar, seed.cookiejar)
        self.assertEqual(sessions[0].options, seed.options)
        self.assertEqual(cleaned, 0)
        self.assertIn("reuse=fresh-metadata", logs)
        self.assertIn("phase=audio-transfer", logs)
        self.assertNotIn("PRIVATE", logs)

    def test_expired_download_refreshes_once_before_normal_bounded_attempts(self):
        result, calls, sessions, cleaned, logs = self.run_download(self.seed(), fail_seed=True)
        self.assertEqual(result, "audio.webm")
        self.assertEqual(calls, [("download", True, "seed"), ("extract", False), ("download", True, "fresh")])
        self.assertEqual(len(sessions), 2)
        self.assertEqual(cleaned, 1)
        self.assertIn("phase=download-seed-refresh", logs)
        self.assertNotIn("PRIVATE", logs)
        self.assertNotIn("signed.test", logs)

    def test_no_seed_measures_extraction_and_transfer_separately(self):
        _, calls, _, _, logs = self.run_download()
        self.assertEqual(calls, [("extract", False), ("download", True, "fresh")])
        self.assertIn("phase=download-metadata", logs)
        self.assertIn("phase=audio-transfer", logs)
        self.assertIn("track=H5v3kku4y6Q", logs)

    def test_real_ytdlp_info_processing_selects_best_audio_without_extractor(self):
        info = {
            "id": "H5v3kku4y6Q", "title": "Offline fixture", "webpage_url": URL,
            "extractor": "youtube", "extractor_key": "Youtube", "duration": 238,
            "formats": [
                {"format_id": "low", "url": "https://cdn.test/low", "ext": "webm", "acodec": "opus", "vcodec": "none", "abr": 64},
                {"format_id": "high", "url": "https://cdn.test/high", "ext": "webm", "acodec": "opus", "vcodec": "none", "abr": 160},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            options = {"format": "bestaudio/best", "quiet": True, "cachedir": False,
                       "proxy": "", "outtmpl": str(Path(directory) / "raw.%(ext)s")}
            with yt_dlp.YoutubeDL(options) as ydl, patch.object(
                ydl, "extract_info", side_effect=AssertionError("Must not contact extractor")
            ), patch.object(ydl, "process_info") as download:
                result = cache_manager._download_resolved_audio(ydl, info, "H5v3kku4y6Q")
            self.assertEqual(download.call_args.args[0]["format_id"], "high")
            self.assertEqual(result, str(Path(directory) / "raw.webm"))


class QualityAndCacheTimingTests(unittest.TestCase):
    STATS = {"input_i": "-20", "input_tp": "-6", "input_lra": "5", "input_thresh": "-30", "target_offset": "0"}

    def test_two_pass_filter_and_opus_quality_are_unchanged(self):
        with patch.object(cache_manager.subprocess, "run", return_value=SimpleNamespace(stderr=json.dumps(self.STATS))) as run:
            measured = cache_manager.measure_loudness_sync("raw.webm")
        self.assertEqual(measured, self.STATS)
        self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        with patch.object(cache_manager.subprocess, "run", return_value=SimpleNamespace(stderr="Normalization Type: Linear\n")) as run:
            with self.assertLogs("cache_manager", level="INFO") as logs:
                cache_manager.normalize_and_encode_sync("raw.webm", "final.opus", self.STATS)
        command = run.call_args.args[0]
        for flag, value in (("-c:a", "libopus"), ("-b:a", "160k"), ("-application", "audio"),
                            ("-frame_duration", "20"), ("-packet_loss", "10"), ("-fec", "1")):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn("linear=true", command[command.index("-af") + 1])
        self.assertEqual(run.call_args.kwargs["timeout"], 180)
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertIn("mode=linear", logs.output[0])

    def test_cache_logs_all_phases_without_publishing_early(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, final = Path(directory) / "raw.webm", Path(directory) / "final.opus"
            raw.write_bytes(b"fixture")

            def encode(_raw, staged, stats):
                self.assertEqual(stats, self.STATS)
                self.assertFalse(final.exists())
                Path(staged).write_bytes(b"finished")

            with patch.object(cache_manager, "download_raw_sync", return_value=str(raw)), patch.object(
                cache_manager, "measure_loudness_sync", return_value=self.STATS
            ), patch.object(cache_manager, "normalize_and_encode_sync", side_effect=encode), self.assertLogs("cache_manager", level="INFO") as logs:
                cache_manager.build_cache_sync(URL, "unused", str(final))
            self.assertEqual(final.read_bytes(), b"finished")
            self.assertFalse(raw.exists())
        text = "\n".join(logs.output)
        for phase in ("download-total", "loudness-measure", "normalize-encode", "cache-publish", "cache-build-total"):
            self.assertIn(f"phase={phase}", text)

    def test_encode_errors_still_propagate_and_do_not_claim_success(self):
        with patch.object(cache_manager.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
            with self.assertRaises(subprocess.CalledProcessError):
                cache_manager.normalize_and_encode_sync("raw.webm", "final.opus", self.STATS)


if __name__ == "__main__":
    unittest.main()
