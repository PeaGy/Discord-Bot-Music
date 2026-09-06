import asyncio
from collections import deque
from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import cache_manager
from commands.play import get_song_info
from music.player import _play_next_locked
from music.urls import canonical_youtube_url
import music_library


VIDEO_ID = "H5v3kku4y6Q"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
SHARE_URL = f"https://youtu.be/{VIDEO_ID}?si=test&t=42"


class YouTubeCacheURLTests(unittest.TestCase):
    def test_url_aliases_identify_the_same_video_without_changing_id_case(self):
        for url in (URL, SHARE_URL, f"https://music.youtube.com/watch?v={VIDEO_ID}&list=RD123",
                    f"https://m.youtube.com/watch?v={VIDEO_ID}",
                    f"https://www.youtube.com/shorts/{VIDEO_ID}",
                    f"https://www.youtube.com/live/{VIDEO_ID}"):
            with self.subTest(url=url):
                self.assertEqual(canonical_youtube_url(url), URL)

    def test_non_video_or_lookalike_urls_are_not_cache_aliases(self):
        for query in ("Harry Styles As It Was", "https://youtube.com/playlist?list=123",
                      "https://youtube.com/watch?v=invalid", "https://soundcloud.com/a/b",
                      f"https://youtube.com.evil.test/watch?v={VIDEO_ID}",
                      f"https://youtube.com@evil.test/watch?v={VIDEO_ID}"):
            with self.subTest(query=query):
                self.assertIsNone(canonical_youtube_url(query))


class CacheLookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(cache_manager, "CACHE_DIR", self.directory.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cache_file(self, url=URL, contents=b"test opus"):
        path = Path(cache_manager.get_cache_paths(url)[1])
        path.write_bytes(contents)
        return str(path)

    async def test_share_link_uses_existing_canonical_cache_without_download(self):
        path = self.cache_file()
        inner = Mock()
        inner.is_opus.return_value = True
        with patch.object(cache_manager.discord, "FFmpegOpusAudio", return_value=inner) as ffmpeg, patch.object(
            cache_manager, "ensure_audio_cached", new=AsyncMock()
        ) as download:
            source = await cache_manager.get_audio_source(SHARE_URL)
        ffmpeg.assert_called_once_with(path, codec="copy", options="-vn")
        download.assert_not_awaited()
        self.assertTrue(source.is_opus())
        self.assertIsInstance(source, cache_manager.OpusPrerollAudioSource)
        source.cleanup()

    async def test_cache_only_miss_never_downloads_or_starts_ffmpeg(self):
        with patch.object(cache_manager.discord, "FFmpegOpusAudio") as ffmpeg, patch.object(
            cache_manager, "ensure_audio_cached", new=AsyncMock()
        ) as download:
            self.assertIsNone(await cache_manager.get_cached_audio_source(URL))
        ffmpeg.assert_not_called()
        download.assert_not_awaited()

    async def test_empty_deleted_and_wrong_version_cache_are_misses(self):
        path = self.cache_file(contents=b"")
        self.assertIsNone(cache_manager.find_cached_audio_path(URL))
        Path(path).unlink()
        self.assertIsNone(cache_manager.find_cached_audio_path(URL))
        self.cache_file()
        with patch.object(cache_manager, "CACHE_FORMAT_VERSION", "next_format"):
            self.assertIsNone(cache_manager.find_cached_audio_path(URL))

    async def test_legacy_exact_url_hash_is_still_found(self):
        path = self.cache_file(SHARE_URL)
        self.assertEqual(cache_manager.find_cached_audio_path(SHARE_URL), path)

    async def test_preload_and_ensure_reuse_canonical_file_for_alias(self):
        path = self.cache_file()
        with patch.object(cache_manager, "build_cache_sync") as build:
            self.assertEqual(await cache_manager.ensure_audio_cached(SHARE_URL), path)
            await cache_manager.preload_audio(SHARE_URL, delay=30)
        build.assert_not_called()

    async def test_in_progress_encode_is_not_published_as_a_cache_hit(self):
        raw = Path(self.directory.name) / "raw.webm"
        raw.write_bytes(b"raw audio")
        final = cache_manager.get_cache_paths(URL)[1]

        def encode(raw_path, staged_path, stats):
            Path(staged_path).write_bytes(b"partial opus")
            self.assertIsNone(cache_manager.find_cached_audio_path(URL))
            Path(staged_path).write_bytes(b"complete opus")

        with patch.object(cache_manager, "download_raw_sync", return_value=str(raw)), patch.object(
            cache_manager, "measure_loudness_sync", return_value={}
        ), patch.object(cache_manager, "normalize_and_encode_sync", side_effect=encode):
            cache_manager.build_cache_sync(URL, "unused", final)
        self.assertEqual(cache_manager.find_cached_audio_path(URL), final)
        self.assertEqual(Path(final).read_bytes(), b"complete opus")
        self.assertEqual(list(Path(self.directory.name).iterdir()), [Path(final)])

    async def test_failed_encode_keeps_old_file_and_removes_partial_output(self):
        final = self.cache_file(contents=b"existing cache")
        raw = Path(self.directory.name) / "raw.webm"
        raw.write_bytes(b"raw audio")

        def fail(raw_path, staged_path, stats):
            Path(staged_path).write_bytes(b"partial opus")
            raise RuntimeError("encode failed")

        with patch.object(cache_manager, "download_raw_sync", return_value=str(raw)), patch.object(
            cache_manager, "measure_loudness_sync", return_value={}
        ), patch.object(cache_manager, "normalize_and_encode_sync", side_effect=fail):
            with self.assertRaises(RuntimeError):
                cache_manager.build_cache_sync(URL, "unused", final)
        self.assertEqual(Path(final).read_bytes(), b"existing cache")
        self.assertEqual(list(Path(self.directory.name).iterdir()), [Path(final)])


class CachedMetadataTests(unittest.TestCase):
    def test_direct_cached_video_skips_all_youtube_metadata_requests(self):
        known = {"title": "Harry Styles - As It Was", "author": "Harry Styles", "duration": 167,
                 "source": "spotify", "search_query": "unused old query", "thumbnail": None}
        with patch("commands.play.find_cached_audio_path", return_value="audio.opus"), patch(
            "commands.play.music_library.find_known_track_sync", return_value=known
        ) as lookup, patch("commands.play.extract_info_with_retry") as extract:
            song = get_song_info(SHARE_URL)
        lookup.assert_called_once_with(URL)
        extract.assert_not_called()
        self.assertEqual(song["title"], known["title"])
        self.assertEqual(song["duration"], 167)
        self.assertEqual(song["source"], "youtube")
        self.assertNotIn("search_query", song)
        self.assertNotIn("youtube_metadata_failed", song)

    def test_legacy_cache_without_metadata_remains_playable_without_network(self):
        with patch("commands.play.find_cached_audio_path", return_value="audio.opus"), patch(
            "commands.play.music_library.find_known_track_sync", return_value=None
        ), patch("commands.play.extract_info_with_retry") as extract:
            song = get_song_info(URL)
        extract.assert_not_called()
        self.assertIn("bản đã lưu", song["title"])
        self.assertEqual(song["url"], URL)

    def test_keyword_still_resolves_the_requested_song(self):
        with patch("commands.play.find_cached_audio_path") as cache, patch(
            "commands.play.extract_info_with_retry",
            return_value={"title": "As It Was", "webpage_url": URL,
                          "formats": [{"url": "https://cdn.test/audio", "acodec": "opus"}]},
        ) as extract:
            song = get_song_info("Harry Styles As It Was")
        cache.assert_called_once_with(URL)  # Only after search identifies a video.
        self.assertEqual(extract.call_args.args[0], "ytsearch1:Harry Styles As It Was")
        self.assertEqual(song["url"], URL)

    def test_keyword_metadata_with_cached_audio_stops_client_retry_cycle(self):
        info = {"title": "As It Was", "webpage_url": URL, "formats": []}

        def extract(query, options, **kwargs):
            self.assertEqual(query, "ytsearch1:Harry Styles As It Was")
            # The shared retry layer accepts this response immediately even
            # though there are no remote audio formats.
            self.assertTrue(kwargs["result_validator"](info))
            return info

        with patch("commands.play.find_cached_audio_path", return_value="audio.opus"), patch(
            "commands.play.audio_fallback_enabled", return_value=True
        ), patch("commands.play.extract_info_with_retry", side_effect=extract) as network:
            song = get_song_info("Harry Styles As It Was")
        network.assert_called_once()
        self.assertNotIn("youtube_metadata_failed", song)


class KnownTrackMetadataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = str(Path(self.directory.name) / "library.db")
        patcher = patch.object(music_library, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_missing_database_is_not_created(self):
        self.assertIsNone(music_library.find_known_track_sync(URL))
        self.assertFalse(Path(self.db_path).exists())

    async def test_older_database_without_music_tables_is_left_unchanged(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("CREATE TABLE legacy (value TEXT)")
            db.commit()
        before = Path(self.db_path).read_bytes()
        self.assertIsNone(music_library.find_known_track_sync(URL))
        self.assertEqual(Path(self.db_path).read_bytes(), before)

    async def test_exact_known_track_only_returns_metadata_and_skips_placeholder(self):
        await music_library.init_db()
        original = {"title": "As It Was", "author": "Harry Styles", "url": URL,
                    "duration": 167, "source": "youtube"}
        await music_library.record_recent(123, original)
        await music_library.record_recent(123, {**original, "title": f"YouTube (bản đã lưu: {VIDEO_ID})"})
        found = music_library.find_known_track_sync(URL)
        self.assertEqual(found["title"], "As It Was")
        self.assertNotIn("guild_id", found)
        self.assertNotIn("requester_id", found)
        self.assertIsNone(music_library.find_known_track_sync("https://www.youtube.com/watch?v=zz2a9Q2Wru0"))


class PlayerCachePriorityTests(unittest.IsolatedAsyncioTestCase):
    async def run_player(self, song, *, cached=None, cache_error=None):
        state = SimpleNamespace(queue=deque([song]), history=[], text_channel=None,
                                now_playing_message=None, loop_mode="off", autoplay=False)
        vc = Mock()
        vc.guild = SimpleNamespace(id=123)
        vc.current_volume = 1.0
        vc.is_connected.return_value = True
        bot = SimpleNamespace(loop=asyncio.get_running_loop(), user=Mock())
        cache = AsyncMock(return_value=cached, side_effect=cache_error)
        fallback = AsyncMock(return_value=None)
        downloaded = Mock()
        download = AsyncMock(return_value=downloaded)
        with ExitStack() as stack:
            stack.enter_context(patch("music.player.cancel_idle_timer"))
            stack.enter_context(patch("music.player.get_cached_audio_source", cache))
            stack.enter_context(patch("music.player.get_audio_source", download))
            stack.enter_context(patch("music.player._try_audio_fallback", fallback))
            extract = stack.enter_context(patch("music.player.extract_info_with_retry", return_value={"url": URL}))
            hint = stack.enter_context(patch("music.player._cached_audio_fallback_hint", return_value=(None, None)))
            volume = stack.enter_context(patch("music.player.discord.PCMVolumeTransformer"))
            temp = stack.enter_context(patch("music.player.get_long_audio_source", new=AsyncMock()))
            stack.enter_context(patch("music.player.discord.FFmpegPCMAudio", return_value=Mock()))
            loading = stack.enter_context(patch("music.player.create_loading_panel"))
            stack.enter_context(patch("music.player.MusicControl"))
            stack.enter_context(patch("music.player.create_radio_panel"))
            stack.enter_context(patch("music.player.send_panel_message", new=AsyncMock(return_value=Mock())))
            stack.enter_context(patch("music.player._record_recent_safely", new=AsyncMock()))
            await _play_next_locked(bot, vc, Mock(), state)
            await asyncio.sleep(0)
        return SimpleNamespace(vc=vc, cache=cache, fallback=fallback, download=download,
                               extract=extract, hint=hint, volume=volume, loading=loading, temp=temp)

    async def test_cache_precedes_failed_metadata_and_stale_fallback_hint(self):
        song = {"title": "As It Was", "url": URL, "duration": 0, "source": "youtube",
                "youtube_metadata_failed": True, "fallback_source": "soundcloud",
                "fallback_url": "https://soundcloud.com/old/hint", "stream_only": True}
        cached = Mock()
        calls = await self.run_player(song, cached=cached)
        calls.cache.assert_awaited_once_with(URL)
        calls.fallback.assert_not_awaited()
        calls.download.assert_not_awaited()
        calls.extract.assert_not_called()
        calls.hint.assert_not_called()
        calls.volume.assert_not_called()  # Cached Opus must not be re-encoded as PCM.
        calls.loading.assert_not_called()
        self.assertIs(calls.vc.play.call_args.args[0], cached)
        self.assertNotIn("fallback_source", song)
        self.assertNotIn("youtube_metadata_failed", song)

    async def test_existing_audio_precedes_long_or_spotify_lookup(self):
        for source in ("youtube", "spotify"):
            with self.subTest(source=source):
                cached = Mock()
                calls = await self.run_player({"title": "Track", "url": URL, "duration": 900,
                                               "source": source, "search_query": "Track Artist"}, cached=cached)
                calls.extract.assert_not_called()
                calls.temp.assert_not_awaited()
                calls.fallback.assert_not_awaited()
                self.assertIs(calls.vc.play.call_args.args[0], cached)

    async def test_miss_or_open_error_keeps_normal_download_path(self):
        for error in (None, OSError("cache file disappeared")):
            with self.subTest(error=error):
                calls = await self.run_player({"title": "Track", "url": URL, "duration": 180,
                                               "source": "youtube"}, cache_error=error)
                calls.download.assert_awaited_once_with(URL)
                calls.fallback.assert_not_awaited()

    async def test_radio_does_not_probe_cache(self):
        calls = await self.run_player({"title": "Radio", "url": "https://radio.test/live",
                                       "source": "radio", "duration": 0})
        calls.cache.assert_not_awaited()
        calls.download.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
