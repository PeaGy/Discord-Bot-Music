import os
import unittest
from unittest.mock import patch

from music import source_fallback
from music.player import (
    _can_use_audio_fallback,
    _can_use_soundcloud_fallback,
    _try_audio_fallback,
    _try_soundcloud_fallback,
)


class SoundCloudCandidateTests(unittest.TestCase):
    def setUp(self):
        source_fallback.clear_soundcloud_fallback_cache()

    def test_matching_original_is_accepted(self):
        song = {
            "title": "Heat Waves",
            "author": "Glass Animals",
            "duration": 238,
        }
        candidate = {
            "title": "Glass Animals - Heat Waves",
            "uploader": "Glass Animals",
            "duration": 237,
        }
        score = source_fallback.score_soundcloud_candidate(song, candidate)
        self.assertIsNotNone(score)
        self.assertGreaterEqual(score, 0.72)

    def test_youtube_style_official_title_matches_short_soundcloud_title(self):
        song = {
            "title": "Glass Animals - Heat Waves (Official Video)",
            "author": "GlassAnimalsVEVO",
            "duration": 238,
        }
        candidate = {
            "title": "Heat Waves",
            "uploader": "Glass Animals",
            "duration": 237,
        }
        score = source_fallback.score_soundcloud_candidate(song, candidate)
        self.assertIsNotNone(score)
        self.assertGreaterEqual(score, 0.72)

    def test_word_containing_live_is_not_mistaken_for_live_version(self):
        song = {"title": "Alive", "author": "Sia", "duration": 263}
        candidate = {"title": "Alive", "uploader": "Sia", "duration": 263}
        self.assertIsNotNone(
            source_fallback.score_soundcloud_candidate(song, candidate)
        )

    def test_unrequested_remix_is_rejected(self):
        song = {
            "title": "Heat Waves",
            "author": "Glass Animals",
            "duration": 238,
        }
        candidate = {
            "title": "Heat Waves (Nightcore Remix)",
            "uploader": "Glass Animals",
            "duration": 238,
        }
        self.assertIsNone(
            source_fallback.score_soundcloud_candidate(song, candidate)
        )

    def test_large_duration_difference_is_rejected(self):
        song = {"title": "Heat Waves", "author": "Glass Animals", "duration": 238}
        candidate = {
            "title": "Heat Waves",
            "uploader": "Glass Animals",
            "duration": 300,
        }
        self.assertIsNone(
            source_fallback.score_soundcloud_candidate(song, candidate)
        )

    def test_search_resolves_stream_without_downloading_and_reuses_match_hint(self):
        calls = []

        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, query, *, download):
                calls.append((query, download, self.options))
                if str(query).startswith("scsearch"):
                    return {
                        "entries": [
                            {
                                "title": "Heat Waves Nightcore Remix",
                                "uploader": "Someone",
                                "duration": 238,
                                "webpage_url": "https://soundcloud.com/wrong/remix",
                            },
                            {
                                "title": "Glass Animals - Heat Waves",
                                "uploader": "Glass Animals",
                                "duration": 237,
                                "webpage_url": "https://soundcloud.com/glassanimals/heat-waves",
                            },
                        ]
                    }
                return {
                    "title": "Heat Waves",
                    "uploader": "Glass Animals",
                    "duration": 237,
                    "webpage_url": query,
                    "url": "https://cf-media.sndcdn.com/audio.mp3",
                }

        song = {
            "title": "Heat Waves",
            "author": "Glass Animals",
            "duration": 238,
        }
        with patch.object(source_fallback.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            first = source_fallback.resolve_soundcloud_fallback_sync(song)
            second = source_fallback.resolve_soundcloud_fallback_sync(song)

        self.assertEqual(
            first["webpage_url"],
            "https://soundcloud.com/glassanimals/heat-waves",
        )
        self.assertEqual(second["stream_url"], first["stream_url"])
        self.assertEqual(len(calls), 3)
        self.assertTrue(str(calls[0][0]).startswith("scsearch5:"))
        self.assertEqual(
            calls[1][0],
            "https://soundcloud.com/glassanimals/heat-waves",
        )
        self.assertEqual(calls[2][0], calls[1][0])
        self.assertTrue(all(download is False for _, download, _ in calls))

    def test_audius_search_uses_strict_match_and_reuses_track_id(self):
        song = {
            "title": "Heat Waves",
            "author": "Glass Animals",
            "duration": 238,
        }
        matching = {
            "id": "track123",
            "title": "Glass Animals - Heat Waves",
            "duration": 237,
            "permalink": "/glassanimals/heat-waves-123",
            "user": {"name": "Glass Animals", "handle": "glassanimals"},
            "access": {"stream": True},
        }
        calls = []

        def fake_api(path, params=None):
            calls.append((path, params))
            if path == "/tracks/search":
                return [
                    {
                        **matching,
                        "id": "wrong",
                        "title": "Heat Waves Nightcore Remix",
                        "permalink": "/someone/heat-waves-nightcore",
                    },
                    matching,
                ]
            if path == "/tracks/track123":
                return matching
            raise AssertionError(path)

        with patch.object(source_fallback, "_audius_api_data", side_effect=fake_api):
            first = source_fallback.resolve_audius_fallback_sync(song)
            second = source_fallback.resolve_audius_fallback_sync(song)

        self.assertEqual(first["track_id"], "track123")
        self.assertIn("/tracks/track123/stream?", first["stream_url"])
        self.assertEqual(second["webpage_url"], first["webpage_url"])
        self.assertEqual(calls[0][0], "/tracks/search")
        self.assertEqual(calls[1][0], "/tracks/track123")

    def test_audio_chain_moves_from_soundcloud_to_audius(self):
        audius = {
            "stream_url": "https://api.audius.co/v1/tracks/id/stream",
            "webpage_url": "https://audius.co/artist/song",
            "track_id": "id",
        }
        env = {
            "YTDLP_SOUNDCLOUD_FALLBACK": "true",
            "YTDLP_AUDIUS_FALLBACK": "true",
        }
        with patch.dict(os.environ, env):
            with patch.object(
                source_fallback,
                "resolve_soundcloud_fallback_sync",
                return_value=None,
            ) as soundcloud:
                with patch.object(
                    source_fallback,
                    "resolve_audius_fallback_sync",
                    return_value=audius,
                ) as audius_resolver:
                    result = source_fallback.resolve_audio_fallback_sync(
                        {"title": "Song"}
                    )

        soundcloud.assert_called_once()
        audius_resolver.assert_called_once()
        self.assertEqual(result["fallback_source"], "audius")
        self.assertEqual(result["fallback_locator"], "id")


class SoundCloudPlayerRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_youtube_error_marks_stream_only_fallback(self):
        song = {
            "title": "Heat Waves",
            "author": "Glass Animals",
            "duration": 238,
            "source": "youtube",
            "url": "https://www.youtube.com/watch?v=mRD0-GxqHVo",
        }
        resolved = {
            "stream_url": "https://cf-media.sndcdn.com/audio.mp3",
            "webpage_url": "https://soundcloud.com/glassanimals/heat-waves",
            "title": "Heat Waves",
            "artist": "Glass Animals",
            "duration": 237,
            "http_headers": {},
            "fallback_source": "soundcloud",
            "fallback_locator": "https://soundcloud.com/glassanimals/heat-waves",
        }
        with patch.dict(os.environ, {"YTDLP_SOUNDCLOUD_FALLBACK": "true"}):
            with patch(
                "music.player.resolve_audio_fallback_sync",
                return_value=resolved,
            ):
                result = await _try_soundcloud_fallback(
                    song,
                    error=RuntimeError("HTTP Error 403: Forbidden"),
                )

        self.assertEqual(result, resolved)
        self.assertTrue(song["stream_only"])
        self.assertEqual(song["fallback_source"], "soundcloud")
        self.assertEqual(song["url"], "https://www.youtube.com/watch?v=mRD0-GxqHVo")

    async def test_audius_only_fallback_marks_stream_only_source(self):
        song = {
            "title": "Track",
            "author": "Artist",
            "duration": 180,
            "source": "youtube",
            "url": "https://www.youtube.com/watch?v=mRD0-GxqHVo",
        }
        resolved = {
            "stream_url": "https://api.audius.co/v1/tracks/id/stream",
            "webpage_url": "https://audius.co/artist/track",
            "track_id": "id",
            "fallback_source": "audius",
            "fallback_locator": "id",
        }
        env = {
            "YTDLP_SOUNDCLOUD_FALLBACK": "",
            "YTDLP_AUDIUS_FALLBACK": "true",
        }
        with patch.dict(os.environ, env):
            with patch(
                "music.player.resolve_audio_fallback_sync",
                return_value=resolved,
            ):
                result = await _try_audio_fallback(
                    song,
                    error=RuntimeError("HTTP Error 403: Forbidden"),
                )

        self.assertEqual(result, resolved)
        self.assertTrue(song["stream_only"])
        self.assertEqual(song["fallback_source"], "audius")
        self.assertEqual(song["fallback_locator"], "id")

    def test_non_transient_or_non_youtube_source_does_not_fallback(self):
        with patch.dict(os.environ, {"YTDLP_SOUNDCLOUD_FALLBACK": "true"}):
            self.assertFalse(
                _can_use_soundcloud_fallback(
                    {"source": "youtube"},
                    ValueError("FFmpeg binary missing"),
                )
            )
            self.assertFalse(
                _can_use_soundcloud_fallback(
                    {"source": "soundcloud"},
                    RuntimeError("HTTP Error 403: Forbidden"),
                )
            )

    def test_audio_fallback_accepts_audius_only_configuration(self):
        env = {
            "YTDLP_SOUNDCLOUD_FALLBACK": "",
            "YTDLP_AUDIUS_FALLBACK": "true",
        }
        with patch.dict(os.environ, env):
            self.assertTrue(
                _can_use_audio_fallback(
                    {"source": "youtube"},
                    RuntimeError("HTTP Error 429: Too Many Requests"),
                )
            )


if __name__ == "__main__":
    unittest.main()
