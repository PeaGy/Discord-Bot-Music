import unittest
from unittest.mock import patch

from commands.play import get_song_info


class PlayMetadataFallbackTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("commands.play.find_cached_audio_path", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_format_youtube_metadata_is_kept_for_soundcloud(self):
        info = {
            "title": "Heat Waves",
            "uploader": "Glass Animals",
            "duration": 238,
            "webpage_url": "https://www.youtube.com/watch?v=mRD0-GxqHVo",
            "thumbnail": "https://i.ytimg.com/example.jpg",
            "formats": [],
        }
        with patch("commands.play.audio_fallback_enabled", return_value=True):
            with patch(
                "commands.play.extract_info_with_retry",
                return_value=info,
            ) as extract:
                song = get_song_info(
                    "https://www.youtube.com/watch?v=mRD0-GxqHVo"
                )

        self.assertTrue(song["youtube_metadata_failed"])
        self.assertEqual(song["title"], "Heat Waves")
        self.assertEqual(song["source"], "youtube")
        self.assertTrue(extract.call_args.args[1]["ignore_no_formats_error"])

    def test_playable_youtube_result_keeps_normal_cache_route(self):
        info = {
            "title": "Heat Waves",
            "uploader": "Glass Animals",
            "duration": 238,
            "webpage_url": "https://www.youtube.com/watch?v=mRD0-GxqHVo",
            "formats": [
                {
                    "url": "https://googlevideo.example/audio",
                    "acodec": "opus",
                }
            ],
        }
        with patch("commands.play.audio_fallback_enabled", return_value=True):
            with patch("commands.play.extract_info_with_retry", return_value=info):
                song = get_song_info(
                    "https://www.youtube.com/watch?v=mRD0-GxqHVo"
                )

        self.assertNotIn("youtube_metadata_failed", song)

    def test_blocked_plain_search_becomes_soundcloud_search_seed(self):
        with patch("commands.play.audio_fallback_enabled", return_value=True):
            with patch(
                "commands.play.extract_info_with_retry",
                side_effect=RuntimeError(
                    "Sign in to confirm you're not a bot"
                ),
            ):
                song = get_song_info("Glass Animals Heat Waves")

        self.assertEqual(song["title"], "Glass Animals Heat Waves")
        self.assertEqual(song["author"], "Unknown")
        self.assertTrue(song["youtube_metadata_failed"])

    def test_blocked_direct_url_without_metadata_is_not_searched_by_video_id(self):
        with patch("commands.play.audio_fallback_enabled", return_value=True):
            with patch(
                "commands.play.extract_info_with_retry",
                side_effect=RuntimeError("HTTP Error 403: Forbidden"),
            ):
                with self.assertRaises(RuntimeError):
                    get_song_info(
                        "https://www.youtube.com/watch?v=mRD0-GxqHVo"
                    )

    def test_disabled_fallback_preserves_old_extractor_options(self):
        info = {
            "title": "Heat Waves",
            "uploader": "Glass Animals",
            "duration": 238,
            "webpage_url": "https://www.youtube.com/watch?v=mRD0-GxqHVo",
            "formats": [],
        }
        with patch("commands.play.audio_fallback_enabled", return_value=False):
            with patch(
                "commands.play.extract_info_with_retry",
                return_value=info,
            ) as extract:
                song = get_song_info(
                    "https://www.youtube.com/watch?v=mRD0-GxqHVo"
                )

        self.assertNotIn("ignore_no_formats_error", extract.call_args.args[1])
        self.assertNotIn("youtube_metadata_failed", song)


if __name__ == "__main__":
    unittest.main()
