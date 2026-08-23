import os
import unittest
from unittest.mock import patch

from ytdlp_support import (
    should_use_long_audio_temp,
    youtube_proxy_enabled,
    youtube_ydl_options,
)


class YoutubeYdlOptionsTests(unittest.TestCase):
    def test_proxy_detection_is_explicit(self):
        with patch.dict(os.environ, {"YTDLP_PROXY": ""}):
            self.assertFalse(youtube_proxy_enabled())
        with patch.dict(
            os.environ,
            {"YTDLP_PROXY": "socks5://127.0.0.1:40000"},
        ):
            self.assertTrue(youtube_proxy_enabled())

    def test_long_audio_temp_only_applies_to_proxied_non_radio_tracks(self):
        with patch.dict(os.environ, {"YTDLP_PROXY": ""}):
            self.assertFalse(should_use_long_audio_temp(6499))

        with patch.dict(
            os.environ,
            {"YTDLP_PROXY": "socks5://127.0.0.1:40000"},
        ):
            self.assertFalse(should_use_long_audio_temp(600))
            self.assertFalse(should_use_long_audio_temp(6499, is_radio=True))
            self.assertTrue(should_use_long_audio_temp(6499))

    def test_home_defaults_are_untouched(self):
        env_names = (
            "YTDLP_PROXY",
            "YTDLP_JS_RUNTIME",
            "YTDLP_REMOTE_COMPONENTS",
            "YTDLP_BGUTIL_URL",
        )
        with patch.dict(os.environ, {name: "" for name in env_names}):
            base = {"extractor_args": {"youtube": {"player_client": ["tv"]}}}
            self.assertEqual(youtube_ydl_options(base), base)

    def test_bgutil_ipv6_url_is_forwarded_to_provider(self):
        env = {
            "YTDLP_PROXY": "socks5://127.0.0.1:40000",
            "YTDLP_JS_RUNTIME": "node",
            "YTDLP_REMOTE_COMPONENTS": "ejs:github",
            "YTDLP_BGUTIL_URL": "http://[::1]:4416",
            "YTDLP_COOKIE_FILE": "",
        }
        with patch.dict(os.environ, env, clear=False):
            options = youtube_ydl_options({})

        self.assertEqual(
            options["extractor_args"],
            {"youtubepot-bgutilhttp": {"base_url": ["http://[::1]:4416"]}},
        )


if __name__ == "__main__":
    unittest.main()
