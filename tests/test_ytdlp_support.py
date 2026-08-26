import os
import sys
import types
import unittest
from unittest.mock import patch

from ytdlp_support import (
    extract_info_with_retry,
    is_transient_ytdlp_error,
    should_use_long_audio_temp,
    youtube_player_clients,
    youtube_proxy_enabled,
    youtube_ydl_options,
)


class YoutubeYdlOptionsTests(unittest.TestCase):
    def test_bgutil_default_clients_have_an_embedded_fallback(self):
        env = {
            "YTDLP_BGUTIL_URL": "http://127.0.0.1:4416",
            "YTDLP_YOUTUBE_CLIENT": "",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                youtube_player_clients(),
                ("web_embedded", "mweb"),
            )

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
            "YTDLP_YOUTUBE_CLIENT",
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
            {
                "youtube": {
                    "player_client": ["web_embedded", "mweb"]
                },
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://[::1]:4416"]
                },
            },
        )

    def test_youtube_client_can_be_overridden(self):
        env = {
            "YTDLP_BGUTIL_URL": "http://127.0.0.1:4416",
            "YTDLP_YOUTUBE_CLIENT": "web_embedded,mweb",
            "YTDLP_COOKIE_FILE": "",
        }
        with patch.dict(os.environ, env, clear=False):
            options = youtube_ydl_options({})

        self.assertEqual(
            options["extractor_args"]["youtube"],
            {"player_client": ["web_embedded", "mweb"]},
        )


class YoutubeMetadataRetryTests(unittest.TestCase):
    def test_transient_403_uses_a_fresh_session(self):
        state = {"sessions": 0, "calls": 0}

        class FakeYoutubeDL:
            def __init__(self, _options):
                state["sessions"] += 1

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, query, *, download):
                state["calls"] += 1
                if state["calls"] == 1:
                    raise RuntimeError("HTTP Error 403: Forbidden")
                return {"query": query, "download": download}

        fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        with patch.dict(sys.modules, {"yt_dlp": fake_module}):
            with patch("ytdlp_support.time.sleep") as mocked_sleep:
                result = extract_info_with_retry(
                    "ytsearch1:Heat Waves",
                    {"quiet": True},
                    retry_delay=2,
                )

        self.assertEqual(result["query"], "ytsearch1:Heat Waves")
        self.assertEqual(state, {"sessions": 2, "calls": 2})
        mocked_sleep.assert_called_once_with(2.0)

    def test_non_network_error_is_not_retried(self):
        self.assertFalse(is_transient_ytdlp_error(ValueError("no results")))
        self.assertTrue(
            is_transient_ytdlp_error(RuntimeError("Socks5Error: Host unreachable"))
        )
        self.assertTrue(
            is_transient_ytdlp_error(
                RuntimeError("Sign in to confirm you’re not a bot")
            )
        )


if __name__ == "__main__":
    unittest.main()
