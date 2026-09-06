import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

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

    def test_cover_disclosed_only_in_description_is_rejected(self):
        song = {"title": "Harry Styles - As It Was (Official Video)", "duration": 0}
        candidate = {
            "title": "Harry Styles - As It Was",
            "artist": "The Paper Outlet",
            "description": "Here's our rendition of As It Was ~The Paper Outlet~",
        }
        self.assertIsNone(source_fallback.score_soundcloud_candidate(song, candidate))
        candidate["description"] = "Cover art by Someone. Official release."
        self.assertGreaterEqual(source_fallback.score_soundcloud_candidate(song, candidate), 0.72)

    def test_resolved_cover_is_rejected_even_when_cached_and_search_title_matches(self):
        song = {"title": "Harry Styles - As It Was", "duration": 0}
        cover_url = "https://soundcloud.com/thepaperoutlet/intentions-justin-bieber"
        good_url = "https://soundcloud.com/harrystyles/as-it-was"
        cover = {
            "title": song["title"], "webpage_url": cover_url,
            "stream_url": "https://cdn.test/cover.mp3",
            "description": "Here's our rendition of As It Was",
        }
        original = {**cover, "webpage_url": good_url, "description": "Official audio"}
        source_fallback._remember_soundcloud_page(song, cover_url)
        with patch.object(source_fallback, "_search_soundcloud_candidates",
                          return_value=[(0.99, cover_url), (0.95, good_url)]), patch.object(
            source_fallback, "_resolve_soundcloud_page", side_effect=[cover, original]
        ) as resolve:
            result = source_fallback.resolve_soundcloud_fallback_sync(song)
        self.assertEqual(result["webpage_url"], good_url)
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual(source_fallback.get_cached_soundcloud_page(song), good_url)

    def test_full_metadata_duration_mismatch_is_rejected_without_known_versions(self):
        song = {"title": "Heat Waves", "author": "Glass Animals", "duration": 238}
        url = "https://soundcloud.com/artist/song"
        with patch.object(source_fallback, "_search_soundcloud_candidates",
                          return_value=[(0.99, url)]), patch.object(
            source_fallback, "_resolve_soundcloud_page",
            return_value={"title": "Heat Waves", "artist": "Glass Animals",
                          "duration": 30, "webpage_url": url}
        ):
            self.assertIsNone(source_fallback.resolve_soundcloud_fallback_sync(song))
        self.assertIsNone(source_fallback.get_cached_soundcloud_page(song))

    def test_failed_audio_locator_is_skipped_in_cache_and_search(self):
        song = {"title": "Heat Waves", "author": "Glass Animals"}
        bad, good = "https://soundcloud.com/a/expired", "https://soundcloud.com/a/good"
        source_fallback._remember_soundcloud_page(song, bad)
        with patch.object(source_fallback, "_search_soundcloud_candidates",
                          return_value=[(0.99, bad), (0.95, good)]), patch.object(
            source_fallback, "_resolve_soundcloud_page",
            return_value={"title": "Heat Waves", "artist": "Glass Animals", "webpage_url": good}
        ) as resolve:
            result = source_fallback.resolve_soundcloud_fallback_sync(
                song, preferred_page_url=bad, excluded_locators=frozenset({bad})
            )
        resolve.assert_called_once_with(good)
        self.assertEqual(result["webpage_url"], good)

    def test_missing_soundcloud_metadata_is_handled(self):
        ydl = Mock()
        ydl.__enter__ = Mock(return_value=ydl)
        ydl.__exit__ = Mock(return_value=False)
        ydl.extract_info.return_value = None
        with patch.object(source_fallback.yt_dlp, "YoutubeDL", return_value=ydl):
            with self.assertRaisesRegex(ValueError, "stream URL"):
                source_fallback._resolve_soundcloud_page("https://soundcloud.com/a/b")

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
                        {"title": "Song"},
                        excluded=frozenset({("soundcloud", "bad-page"), ("audius", "bad-id")}),
                    )

        soundcloud.assert_called_once()
        audius_resolver.assert_called_once()
        self.assertEqual(soundcloud.call_args.kwargs["excluded_locators"], frozenset({"bad-page"}))
        self.assertEqual(audius_resolver.call_args.kwargs["excluded_locators"], frozenset({"bad-id"}))
        self.assertEqual(result["fallback_source"], "audius")
        self.assertEqual(result["fallback_locator"], "id")


class SoundCloudPlayerRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.audio = Mock()
        patcher = patch("music.player.open_fallback_stream", new=AsyncMock(return_value=self.audio))
        self.open_stream = patcher.start()
        self.addCleanup(patcher.stop)

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

        self.assertEqual(result, {**resolved, "audio_source": self.audio})
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

        self.assertEqual(result, {**resolved, "audio_source": self.audio})
        self.assertTrue(song["stream_only"])
        self.assertEqual(song["fallback_source"], "audius")
        self.assertEqual(song["fallback_locator"], "id")

    async def test_no_audio_tries_next_candidate_without_reusing_failed_url(self):
        song = {"title": "Track", "source": "youtube", "duration": 0}
        first = {"stream_url": "https://cdn.test/bad", "webpage_url": "https://soundcloud.com/a/bad",
                 "fallback_source": "soundcloud", "fallback_locator": "https://soundcloud.com/a/bad"}
        second = {**first, "webpage_url": "https://soundcloud.com/a/good",
                  "fallback_locator": "https://soundcloud.com/a/good", "duration": 174}
        self.open_stream.side_effect = [RuntimeError("EOF"), self.audio]
        with patch.dict(os.environ, {"YTDLP_SOUNDCLOUD_FALLBACK": "true"}), patch(
            "music.player.resolve_audio_fallback_sync", side_effect=[first, second]
        ) as resolve:
            result = await _try_audio_fallback(song)
        self.assertIs(result["audio_source"], self.audio)
        self.assertEqual(resolve.call_args_list[1].kwargs["excluded"],
                         frozenset({("soundcloud", first["fallback_locator"])}))
        self.assertEqual(song["fallback_url"], second["webpage_url"])
        self.assertEqual(song["duration"], 174)

    async def test_all_streams_fail_does_not_mark_song_as_playing_fallback(self):
        song = {"title": "Track", "source": "youtube"}
        self.open_stream.side_effect = TimeoutError("no audio")
        candidates = [{"stream_url": "https://cdn.test/stream", "webpage_url": f"https://soundcloud.com/a/{i}",
                       "fallback_source": "soundcloud"} for i in range(3)]
        with patch.dict(os.environ, {"YTDLP_SOUNDCLOUD_FALLBACK": "true"}), patch(
            "music.player.resolve_audio_fallback_sync", side_effect=candidates
        ) as resolve:
            result = await _try_audio_fallback(song)
        self.assertIsNone(result)
        self.assertEqual(resolve.call_count, 3)
        self.assertNotIn("fallback_source", song)
        self.assertNotIn("stream_only", song)

    async def test_startup_and_reselection_share_the_same_deadline(self):
        song = {"title": "Track", "source": "youtube"}
        now = [0.0]
        candidate = {"stream_url": "https://cdn.test/bad", "webpage_url": "https://soundcloud.com/a/bad",
                     "fallback_source": "soundcloud"}

        async def stall(info, *, timeout):
            now[0] += timeout
            raise TimeoutError()

        self.open_stream.side_effect = stall
        with patch.dict(os.environ, {"YTDLP_SOUNDCLOUD_FALLBACK": "true"}), patch(
            "music.player.resolve_audio_fallback_sync", return_value=candidate
        ) as resolve, patch("music.player.audio_fallback_timeout_seconds", return_value=0.05), patch(
            "music.player.time", Mock(monotonic=lambda: now[0])
        ):
            self.assertIsNone(await _try_audio_fallback(song))
        resolve.assert_called_once()
        self.assertLessEqual(self.open_stream.call_args.kwargs["timeout"], 0.05)

    async def test_cancelled_startup_propagates_without_marking_success(self):
        song = {"title": "Track", "source": "youtube"}
        candidate = {"stream_url": "https://cdn.test/bad", "webpage_url": "https://soundcloud.com/a/bad",
                     "fallback_source": "soundcloud"}
        self.open_stream.side_effect = asyncio.CancelledError()
        with patch.dict(os.environ, {"YTDLP_SOUNDCLOUD_FALLBACK": "true"}), patch(
            "music.player.resolve_audio_fallback_sync", return_value=candidate
        ) as resolve:
            with self.assertRaises(asyncio.CancelledError):
                await _try_audio_fallback(song)
        resolve.assert_called_once()
        self.assertNotIn("stream_only", song)

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
