import os
import tempfile
import time
import unittest
from unittest.mock import patch

import cache_manager


class _DummyOpusSource:
    def __init__(self):
        self.cleanup_calls = 0

    def read(self):
        return b"opus"

    def is_opus(self):
        return True

    def cleanup(self):
        self.cleanup_calls += 1


class TemporaryFileAudioSourceTests(unittest.TestCase):
    def test_cleanup_removes_file_once(self):
        descriptor, path = tempfile.mkstemp(suffix=".webm")
        os.close(descriptor)
        inner = _DummyOpusSource()
        source = cache_manager.TemporaryFileAudioSource(inner, path)

        source.cleanup()
        source.cleanup()

        self.assertFalse(os.path.exists(path))
        self.assertEqual(inner.cleanup_calls, 1)

    def test_stale_cleanup_keeps_recent_file(self):
        with tempfile.TemporaryDirectory() as directory:
            stale_path = os.path.join(directory, "stale.webm")
            recent_path = os.path.join(directory, "recent.webm")
            for path in (stale_path, recent_path):
                with open(path, "wb") as file:
                    file.write(b"audio")
            old_time = time.time() - 100
            os.utime(stale_path, (old_time, old_time))

            with patch.object(cache_manager, "LONG_AUDIO_TEMP_DIR", directory):
                cache_manager.cleanup_stale_long_audio_files(max_age=50)

            self.assertFalse(os.path.exists(stale_path))
            self.assertTrue(os.path.exists(recent_path))


class LongAudioDurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tracks_over_two_hours_are_rejected_before_download(self):
        with self.assertRaises(cache_manager.AudioDownloadError):
            await cache_manager.get_long_audio_source(
                "https://www.youtube.com/watch?v=test",
                cache_manager.LONG_AUDIO_TEMP_MAX_DURATION + 1,
            )


class ShortAudioDownloadRetryTests(unittest.TestCase):
    def test_transient_failure_rotates_to_next_client(self):
        state = {"calls": 0, "clients": []}

        class FakeYoutubeDL:
            def __init__(self, options):
                state["clients"].append(
                    options["extractor_args"]["youtube"]["player_client"]
                )

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, *, download):
                self.assert_download = download
                state["calls"] += 1
                if state["calls"] == 1:
                    raise RuntimeError("HTTP Error 403: Forbidden")
                return {"id": "video-id"}

            @staticmethod
            def prepare_filename(_info):
                return "audio.webm"

        with patch.object(
            cache_manager, "youtube_player_clients", return_value=("web_embedded", "mweb")
        ):
            with patch.object(cache_manager.yt_dlp, "YoutubeDL", FakeYoutubeDL):
                with patch.object(cache_manager.time, "sleep") as mocked_sleep:
                    path = cache_manager.download_raw_sync(
                        "https://www.youtube.com/watch?v=video-id",
                        "audio.%(ext)s",
                    )

        self.assertEqual(path, "audio.webm")
        self.assertEqual(state["calls"], 2)
        self.assertEqual(state["clients"], [["web_embedded"], ["mweb"]])
        mocked_sleep.assert_called_once_with(3)

    def test_home_route_keeps_one_automatic_attempt(self):
        state = {"calls": 0}

        class FailingYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, *, download):
                state["calls"] += 1
                raise RuntimeError("HTTP Error 403: Forbidden")

        with patch.object(cache_manager, "youtube_player_clients", return_value=()):
            with patch.object(cache_manager.yt_dlp, "YoutubeDL", FailingYoutubeDL):
                with self.assertRaises(RuntimeError):
                    cache_manager.download_raw_sync(
                        "https://www.youtube.com/watch?v=video-id",
                        "audio.%(ext)s",
                    )

        self.assertEqual(state["calls"], 1)

    def test_transient_proxy_failures_retry_download_directly(self):
        state = {"calls": 0, "proxies": [], "cookies": [], "clients": []}

        class FakeYoutubeDL:
            def __init__(self, options):
                state["proxies"].append(options.get("proxy"))
                state["cookies"].append(options.get("cookiefile"))
                state["clients"].append(
                    options["extractor_args"]["youtube"]["player_client"]
                )

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, *, download):
                state["calls"] += 1
                if state["calls"] <= 2:
                    raise RuntimeError("HTTP Error 403: Forbidden")
                return {"id": "video-id"}

            @staticmethod
            def prepare_filename(_info):
                return "audio.webm"

        env = {
            "YTDLP_PROXY": "socks5://127.0.0.1:40000",
            "YTDLP_YOUTUBE_DIRECT_FALLBACK": "true",
            "YTDLP_COOKIE_FILE": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(
                cache_manager,
                "youtube_player_clients",
                return_value=("web_embedded", "mweb"),
            ):
                with patch.object(
                    cache_manager.yt_dlp,
                    "YoutubeDL",
                    FakeYoutubeDL,
                ):
                    with patch.object(cache_manager.time, "sleep") as mocked_sleep:
                        path = cache_manager.download_raw_sync(
                            "https://www.youtube.com/watch?v=video-id",
                            "audio.%(ext)s",
                        )

        self.assertEqual(path, "audio.webm")
        self.assertEqual(state["calls"], 3)
        self.assertEqual(
            state["proxies"],
            ["socks5://127.0.0.1:40000", "socks5://127.0.0.1:40000", None],
        )
        self.assertEqual(state["cookies"], [None, None, None])
        self.assertEqual(
            state["clients"],
            [["web_embedded"], ["mweb"], ["web_embedded"]],
        )
        self.assertEqual(mocked_sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
