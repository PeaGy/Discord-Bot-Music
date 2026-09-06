import os
import sys
import types
import unittest
from unittest.mock import patch

from ytdlp_support import (
    audio_fallback_enabled,
    audio_fallback_timeout_seconds,
    audius_fallback_enabled,
    extract_info_with_retry,
    is_transient_ytdlp_error,
    should_use_long_audio_temp,
    soundcloud_fallback_enabled,
    soundcloud_fallback_timeout_seconds,
    soundcloud_fallback_ttl_seconds,
    soundcloud_ydl_options,
    ydl_options_for_player_client,
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

    def test_single_client_option_does_not_mutate_base_options(self):
        base = {
            "quiet": True,
            "extractor_args": {
                "youtube": {"player_client": ["web_embedded", "mweb"]},
                "provider": {"url": ["http://127.0.0.1"]},
            },
        }
        result = ydl_options_for_player_client(base, "mweb")

        self.assertEqual(
            result["extractor_args"]["youtube"]["player_client"],
            ["mweb"],
        )
        self.assertEqual(
            base["extractor_args"]["youtube"]["player_client"],
            ["web_embedded", "mweb"],
        )


class YoutubeMetadataRetryTests(unittest.TestCase):
    def test_transient_403_uses_a_fresh_session(self):
        state = {"sessions": 0, "calls": 0, "clients": []}

        class FakeYoutubeDL:
            def __init__(self, options):
                state["sessions"] += 1
                state["clients"].append(
                    options["extractor_args"]["youtube"]["player_client"]
                )

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
        env = {
            "YTDLP_BGUTIL_URL": "http://127.0.0.1:4416",
            "YTDLP_YOUTUBE_CLIENT": "web_embedded,mweb",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                with patch("ytdlp_support.time.sleep") as mocked_sleep:
                    result = extract_info_with_retry(
                        "ytsearch1:Heat Waves",
                        {"quiet": True},
                        retry_delay=2,
                    )

        self.assertEqual(result["query"], "ytsearch1:Heat Waves")
        self.assertEqual(state["sessions"], 2)
        self.assertEqual(state["calls"], 2)
        self.assertEqual(state["clients"], [["web_embedded"], ["mweb"]])
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

    def test_wrapped_transient_error_is_recognized(self):
        try:
            try:
                raise RuntimeError("HTTP Error 403: Forbidden")
            except RuntimeError as original:
                raise ValueError("Không tải được audio") from original
        except ValueError as wrapped:
            self.assertTrue(is_transient_ytdlp_error(wrapped))


class SoundCloudFallbackOptionsTests(unittest.TestCase):
    def test_fallback_is_opt_in(self):
        with patch.dict(os.environ, {"YTDLP_SOUNDCLOUD_FALLBACK": ""}):
            self.assertFalse(soundcloud_fallback_enabled())
        with patch.dict(os.environ, {"YTDLP_SOUNDCLOUD_FALLBACK": "true"}):
            self.assertTrue(soundcloud_fallback_enabled())

    def test_audius_is_separately_opt_in(self):
        with patch.dict(os.environ, {"YTDLP_AUDIUS_FALLBACK": ""}):
            self.assertFalse(audius_fallback_enabled())
        with patch.dict(
            os.environ,
            {
                "YTDLP_SOUNDCLOUD_FALLBACK": "",
                "YTDLP_AUDIUS_FALLBACK": "true",
            },
        ):
            self.assertTrue(audius_fallback_enabled())
            self.assertTrue(audio_fallback_enabled())

    def test_ttl_is_bounded(self):
        with patch.dict(
            os.environ,
            {"YTDLP_SOUNDCLOUD_FALLBACK_TTL_SECONDS": "10"},
        ):
            self.assertEqual(soundcloud_fallback_ttl_seconds(), 60)
        with patch.dict(
            os.environ,
            {"YTDLP_SOUNDCLOUD_FALLBACK_TTL_SECONDS": "invalid"},
        ):
            self.assertEqual(soundcloud_fallback_ttl_seconds(), 1800)

    def test_timeout_is_bounded(self):
        with patch.dict(
            os.environ,
            {"YTDLP_SOUNDCLOUD_FALLBACK_TIMEOUT_SECONDS": "2"},
        ):
            self.assertEqual(soundcloud_fallback_timeout_seconds(), 5.0)
        with patch.dict(
            os.environ,
            {"YTDLP_SOUNDCLOUD_FALLBACK_TIMEOUT_SECONDS": "invalid"},
        ):
            self.assertEqual(soundcloud_fallback_timeout_seconds(), 20.0)

    def test_shared_timeout_prefers_new_name_and_supports_legacy_name(self):
        with patch.dict(
            os.environ,
            {
                "YTDLP_AUDIO_FALLBACK_TIMEOUT_SECONDS": "12",
                "YTDLP_SOUNDCLOUD_FALLBACK_TIMEOUT_SECONDS": "30",
            },
        ):
            self.assertEqual(audio_fallback_timeout_seconds(), 12.0)
        with patch.dict(
            os.environ,
            {
                "YTDLP_AUDIO_FALLBACK_TIMEOUT_SECONDS": "",
                "YTDLP_SOUNDCLOUD_FALLBACK_TIMEOUT_SECONDS": "14",
            },
        ):
            self.assertEqual(audio_fallback_timeout_seconds(), 14.0)

    def test_soundcloud_stream_does_not_inherit_youtube_proxy(self):
        result = soundcloud_ydl_options(
            {
                "quiet": True,
                "proxy": "socks5://127.0.0.1:40000",
                "cookiefile": "cookies.txt",
                "extractor_args": {"youtube": {"player_client": ["mweb"]}},
            }
        )
        self.assertEqual(result, {"quiet": True})


if __name__ == "__main__":
    unittest.main()
