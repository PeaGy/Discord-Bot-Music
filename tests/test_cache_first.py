import asyncio
from collections import deque
from contextlib import ExitStack, closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

import cache_manager
from music.controls import MusicControl
from music.resolver import get_song_info, resolve_song
from music.player import _play_next_locked, _record_recent_safely, _resolve_spotify_video, preload_track
from music.urls import canonical_youtube_url
import music_library


VIDEO_ID = "H5v3kku4y6Q"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
SHARE_URL = f"https://youtu.be/{VIDEO_ID}?si=test&t=42"


class YouTubeCacheURLTests(unittest.TestCase):
    def test_cached_source_sends_audio_packets_without_headers_or_preroll(self):
        head = b"OpusHead\x01\x02" + bytes(9)
        packets = [head, b"OpusTags" + bytes(8), b"first", b"second"]
        # One Ogg page containing four complete packets.
        page = b"OggS" + bytes(22) + bytes([4]) + bytes(map(len, packets)) + b"".join(packets)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cached.opus"
            path.write_bytes(page)
            with patch.object(cache_manager.discord, "FFmpegOpusAudio") as ffmpeg:
                source = cache_manager._open_cached_opus(path)
                try:
                    self.assertTrue(source.is_opus())
                    self.assertEqual([source.read() for _ in range(3)], [b"first", b"second", b""])
                finally:
                    source.cleanup()
            ffmpeg.assert_not_called()
            path.unlink()

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
    async def test_two_listeners_play_during_one_build_and_download_waits_for_publish(self):
        def page(packets):
            return (b"OggS" + bytes(22) + bytes([len(packets)]) + bytes(map(len, packets))
                    + b"".join(packets))

        head = b"OpusHead\x01\x02" + bytes(9)
        prefix = page([head, b"OpusTags" + bytes(8)]) + page([b"a" * 200] * 200)
        release = threading.Event()
        raw = Path(self.directory.name) / "raw.webm"
        raw.touch()
        final = cache_manager.get_cache_paths(URL)[1]

        def encode(_raw, staged, stats, *, progressive):
            self.assertTrue(progressive)
            Path(staged).write_bytes(prefix)
            if not release.wait(5):
                raise TimeoutError("Playback did not start before publication")
            with open(staged, "ab") as stream:
                stream.write(page([b"last"]))

        with patch.object(cache_manager, "download_raw_sync", return_value=str(raw)) as download, patch.object(
            cache_manager, "measure_loudness_sync", return_value=None
        ), patch.object(cache_manager, "normalize_and_encode_sync", side_effect=encode):
            complete = asyncio.create_task(cache_manager.ensure_audio_cached(URL))
            try:
                first, second = await asyncio.gather(cache_manager.get_audio_source(URL),
                                                     cache_manager.get_audio_source(SHARE_URL))
                self.assertFalse(complete.done())
                self.assertFalse(Path(final).exists())
                self.assertEqual(first.read(), second.read())
                first.cleanup()
            finally:
                release.set()
                await complete
            try:
                self.assertEqual([second.read() for _ in range(199)], [b"a" * 200] * 199)
                self.assertEqual(second.read(), b"last")
                self.assertEqual(second.read(), b"")
            finally:
                second.cleanup()
            download.assert_called_once()

    def test_failed_progressive_encoder_raises_instead_of_clean_eof(self):
        output = cache_manager.GrowingOpusFile(str(Path(self.directory.name) / "final.opus"))
        Path(output.staged_path).write_bytes(b"prefix")
        with output.open_reader() as reader:
            self.assertEqual(reader.read(6), b"prefix")
            output.finish()
            with self.assertRaises(OSError):
                reader.read(1)
        self.assertFalse(Path(output.final_path).exists())

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
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patches = ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(patch.object(music_library, "DB_PATH", str(Path(folder.name) / "library.db")))
        patches.enter_context(patch.object(cache_manager, "CACHE_DIR", folder.name))

    def test_keyword_replay_uses_disk_match_only_while_audio_exists(self):
        result = {"title": "Shikairo Days", "webpage_url": URL, "formats": [{"url": "audio", "acodec": "opus"}]}
        with patch("music.resolver.extract_info_with_retry", return_value=result) as extract:
            first = resolve_song("shikairo days")
            path = Path(cache_manager.get_cache_paths(URL)[1])
            path.write_bytes(b"cached")
            self.assertEqual(resolve_song("  Shikairo   DAYS "), first)
            extract.assert_called_once()
            resolve_song("shikairo days live")
            self.assertEqual(extract.call_count, 2)
            path.unlink()
            resolve_song("shikairo days")
            self.assertEqual(extract.call_count, 3)

    def test_direct_cached_video_skips_all_youtube_metadata_requests(self):
        known = {"title": "Harry Styles - As It Was", "author": "Harry Styles", "duration": 167,
                 "source": "spotify", "search_query": "unused old query", "thumbnail": None}
        with patch("music.resolver.find_cached_audio_path", return_value="audio.opus"), patch(
            "music.resolver.music_library.find_known_track_sync", return_value=known
        ) as lookup, patch("music.resolver.extract_info_with_retry") as extract:
            song = get_song_info(SHARE_URL)
        lookup.assert_called_once_with(URL)
        extract.assert_not_called()
        self.assertEqual(song["title"], known["title"])
        self.assertEqual(song["duration"], 167)
        self.assertEqual(song["source"], "youtube")
        self.assertNotIn("search_query", song)
        self.assertNotIn("youtube_metadata_failed", song)

    def test_legacy_cache_without_metadata_remains_playable_without_network(self):
        with patch("music.resolver.find_cached_audio_path", return_value="audio.opus"), patch(
            "music.resolver.music_library.find_known_track_sync", return_value=None
        ), patch("music.resolver.extract_info_with_retry") as extract:
            song = get_song_info(URL)
        extract.assert_not_called()
        self.assertIn("bản đã lưu", song["title"])
        self.assertEqual(song["url"], URL)

    def test_keyword_still_resolves_the_requested_song(self):
        with patch("music.resolver.find_cached_audio_path") as cache, patch(
            "music.resolver.extract_info_with_retry",
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

        with patch("music.resolver.find_cached_audio_path", return_value="audio.opus"), patch(
            "music.resolver.audio_fallback_enabled", return_value=True
        ), patch("music.resolver.extract_info_with_retry", side_effect=extract) as network:
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

    def test_spotify_html_entities_are_decoded_before_building_search_query(self):
        link = "https://open.spotify.com/track/0KKkJNfGyhkQ5aFogxQAPU"
        page = (
            '<meta property="og:title" content="That&#x27;s What I Like">'
            '<meta property="og:description" content="Bruno Mars &amp; Friends &middot; Album">'
            '<meta property="og:image" content="https://img.test/cover?w=100&amp;h=100">'
        )
        with patch("music.spotify.urllib.request.urlopen") as fetch:
            fetch.return_value.read.return_value = page.encode()
            song = resolve_song(link)
        self.assertEqual(song["title"], "That's What I Like")
        self.assertEqual(song["author"], "Bruno Mars & Friends")
        self.assertEqual(song["thumbnail"], "https://img.test/cover?w=100&h=100")
        self.assertEqual(song["search_query"], "That's What I Like Bruno Mars & Friends audio")

    async def test_existing_spotify_cache_and_history_are_repaired_exactly_once(self):
        track_id = "0KKkJNfGyhkQ5aFogxQAPU"
        link = "https://open.spotify.com/track/" + track_id
        title = "That&#x27;s What I Like"
        author = "Bruno Mars &amp; Friends"
        legacy = {"url": link, "title": title, "author": author, "duration": 206, "source": "spotify",
                  "search_query": f"{title} {author} audio", "playback_url": URL, "playback_duration": 210,
                  "thumbnail": "https://img.test/cover?w=100&amp;h=100&copy=1"}
        await music_library.init_db()
        # Reproduce the schema and stored rows of a library from before this fix.
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("DROP TABLE music_library_migrations")
        music_library.remember_search_match("spotify:" + track_id, URL, legacy)
        await music_library.record_recent(1, legacy)
        await music_library.record_recent(2, {**legacy, "url": SHARE_URL, "source": "youtube"})
        await music_library.init_db()

        with patch.object(cache_manager, "CACHE_DIR", self.directory.name), patch(
            "music.spotify.urllib.request.urlopen"
        ) as fetch:
            Path(cache_manager.get_cache_paths(URL)[1]).write_bytes(b"cached")
            cached = resolve_song(link)
        fetch.assert_not_called()
        self.assertEqual(cached["playback_url"], URL)
        self.assertEqual(cached["playback_duration"], 210)
        recent, = await music_library.list_recent(1)
        inherited, = await music_library.list_recent(2)
        video = music_library.read_search_match("video:" + URL)
        for track in (cached, recent, inherited, video):
            self.assertEqual(track["title"], "That's What I Like")
            self.assertEqual(track["author"], "Bruno Mars & Friends")
            self.assertEqual(track["thumbnail"], "https://img.test/cover?w=100&h=100&copy=1")
        self.assertEqual(cached["search_query"], "That's What I Like Bruno Mars & Friends audio")
        self.assertEqual(recent["search_query"], cached["search_query"])

        # A new, literal entity in an already-decoded title must survive a restart.
        await music_library.record_recent(1, {**cached, "title": "Literal &quot;"})
        await music_library.init_db()
        self.assertEqual((await music_library.list_recent(1))[0]["title"], "Literal &quot;")

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

    async def test_youtube_link_gets_metadata_when_audio_was_cached_through_spotify(self):
        await music_library.init_db()
        track_id = "Ab" * 11
        song = {"title": "As It Was", "author": "Harry Styles", "duration": 167,
                "source": "spotify", "url": "https://open.spotify.com/track/" + track_id,
                "playback_url": URL, "playback_duration": 720, "thumbnail": "https://example.test/image"}
        await music_library.record_recent(123, song)
        music_library.remember_search_match("spotify:" + track_id, URL, song)
        with patch.object(cache_manager, "CACHE_DIR", self.directory.name), patch(
            "music.resolver.extract_info_with_retry"
        ) as extract:
            Path(cache_manager.get_cache_paths(URL)[1]).write_bytes(b"cached")
            resolved = resolve_song(SHARE_URL)

        extract.assert_not_called()
        self.assertEqual(resolved["url"], SHARE_URL)
        self.assertEqual(resolved["source"], "youtube")
        self.assertEqual(resolved["duration"], 720)
        self.assertEqual(music_library.read_search_match("spotify:" + track_id)["duration"], 167)
        for field in ("title", "author", "thumbnail"):
            self.assertEqual(resolved[field], song[field])
        for track in (song, resolved):
            with self.subTest(source=track["source"]), patch("music.controls.temporary_download_mp3") as mp3:
                interaction = SimpleNamespace(response=AsyncMock())
                await MusicControl.download(SimpleNamespace(track=track), interaction, None)
                interaction.response.send_message.assert_awaited_once()
                self.assertIn("10 phút", interaction.response.send_message.await_args.args[0])
                mp3.assert_not_called()

    async def test_youtube_link_fills_missing_duration_without_replacing_known_metadata(self):
        await music_library.init_db()
        known = {"title": "YouTube title", "author": "YouTube channel",
                 "url": URL, "source": "youtube", "duration": 0}
        await music_library.record_recent(123, known)
        self.assertEqual(music_library.find_known_track_sync(URL)["duration"], 0)
        music_library.remember_search_match(
            "spotify:" + "Ab" * 11, URL, {"title": "Spotify title", "duration": 167,
                                          "playback_duration": 720, "playback_url": URL},
        )
        with patch.object(cache_manager, "CACHE_DIR", self.directory.name), patch(
            "music.resolver.extract_info_with_retry"
        ) as extract:
            Path(cache_manager.get_cache_paths(URL)[1]).write_bytes(b"cached")
            resolved = resolve_song(SHARE_URL)
        extract.assert_not_called()
        self.assertEqual(resolved["title"], known["title"])
        self.assertEqual(resolved["author"], known["author"])
        self.assertEqual(resolved["duration"], 720)

    async def test_legacy_spotify_duration_is_unknown_for_cached_youtube_link(self):
        await music_library.init_db()
        legacy = {"title": "Song", "source": "spotify", "duration": 167, "url": URL}
        music_library.remember_search_match("spotify:" + "Ab" * 11, URL, legacy)
        self.assertEqual(music_library.find_known_track_sync(URL)["duration"], 0)
        await music_library.record_recent(123, legacy)
        self.assertEqual(music_library.find_known_track_sync(URL)["duration"], 0)


class SavedSpotifyCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / "library.db"
        patcher = patch.object(music_library, "DB_PATH", str(self.db_path))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.spotify_id = "Ab" * 11
        self.song = {"title": "Song", "author": "Artist", "source": "spotify",
                     "url": "https://open.spotify.com/track/" + self.spotify_id}
        await music_library.init_db()

    async def test_legacy_localized_link_matches_current_spotify_id_without_search_index(self):
        legacy = {**self.song, "url": self.song["url"].replace("/track/", "/intl-vi/track/")}
        old_identity = "spotify:intl-vi/track/" + self.spotify_id.casefold()
        old_key = hashlib.sha256(old_identity.encode()).hexdigest()
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(
                "INSERT INTO music_favorites VALUES (1, 2, ?, 'Song', 'Artist', ?, '', 0, '', 'spotify', CURRENT_TIMESTAMP)",
                (old_key, legacy["url"]),
            )
        self.assertEqual(await music_library.toggle_favorite(1, 2, self.song), "removed")
        self.assertEqual(await music_library.list_favorites(1, 2), [])

    async def test_legacy_spotify_video_matches_current_track_even_without_audio_cache(self):
        legacy = {**self.song, "url": URL}
        music_library.remember_search_match("spotify:" + self.spotify_id, URL, self.song)
        self.assertEqual(await music_library.toggle_favorite(1, 2, legacy), "added")
        self.assertEqual(await music_library.toggle_favorite(1, 2, self.song), "removed")
        self.assertEqual(await music_library.list_favorites(1, 2), [])

        self.assertEqual(await music_library.create_playlist(1, 2, "Mix"), "created")
        self.assertEqual(await music_library.add_track_to_playlist(1, 2, "Mix", legacy), "added")
        self.assertEqual(await music_library.add_track_to_playlist(1, 2, "Mix", self.song), "duplicate")
        self.assertEqual(len(await music_library.get_playlist_tracks(1, 2, "Mix")), 1)

        self.assertEqual(await music_library.create_playlist(1, 2, "New"), "created")
        self.assertEqual(await music_library.add_track_to_playlist(1, 2, "New", self.song), "added")
        self.assertEqual(await music_library.add_track_to_playlist(1, 2, "New", legacy), "duplicate")

    async def test_youtube_favorite_stays_separate_from_spotify_match(self):
        music_library.remember_search_match("spotify:" + self.spotify_id, URL, self.song)
        youtube = {**self.song, "source": "youtube", "url": URL}
        self.assertEqual(await music_library.toggle_favorite(1, 2, youtube), "added")
        self.assertEqual(await music_library.toggle_favorite(1, 2, self.song), "added")
        self.assertEqual(len(await music_library.list_favorites(1, 2)), 2)

    async def test_legacy_replacement_matches_favorites_and_playlists_in_both_directions(self):
        legacy = {**self.song, "url": URL}
        replacement = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
        music_library.remember_search_match("spotify-legacy:" + VIDEO_ID, replacement, legacy)
        music_library.remember_search_match("spotify:" + self.spotify_id, replacement, self.song)
        for name, first, second in (("Old", legacy, self.song), ("New", self.song, legacy)):
            with self.subTest(name=name):
                self.assertEqual(await music_library.toggle_favorite(1, 2, first), "added")
                self.assertEqual(await music_library.toggle_favorite(1, 2, second), "removed")
                await music_library.create_playlist(1, 2, name)
                await music_library.add_track_to_playlist(1, 2, name, first)
                self.assertEqual(await music_library.add_track_to_playlist(1, 2, name, second), "duplicate")

        music_library.remember_search_match("spotify:" + "B2" * 11, URL, legacy)
        self.assertEqual(await music_library.toggle_favorite(1, 2, legacy), "added")
        self.assertEqual(await music_library.toggle_favorite(1, 2, self.song), "added")

    async def test_shared_youtube_match_does_not_delete_ambiguous_legacy_favorite(self):
        for track_id in (self.spotify_id, "B2" * 11):
            music_library.remember_search_match("spotify:" + track_id, URL, self.song)
        legacy = {**self.song, "url": URL}
        self.assertEqual(await music_library.toggle_favorite(1, 2, legacy), "added")
        self.assertEqual(await music_library.toggle_favorite(1, 2, self.song), "added")
        self.assertEqual(len(await music_library.list_favorites(1, 2)), 2)


class PlayerCachePriorityTests(unittest.IsolatedAsyncioTestCase):
    async def run_player(self, song, *, cached=None, cache_error=None, info=None, preload=False,
                         extract_effect=None, download_effect=None, temp_effect=None, record=None,
                         following=()):
        state = SimpleNamespace(queue=deque([song, *following]), history=[], text_channel=None,
                                now_playing_message=None, loop_mode="off", autoplay=False)
        vc = Mock()
        vc.guild = SimpleNamespace(id=123)
        vc.current_volume = 1.0
        vc.is_connected.return_value = True
        bot = SimpleNamespace(loop=asyncio.get_running_loop(), user=Mock())
        cache = AsyncMock(return_value=cached, side_effect=cache_error)
        fallback = AsyncMock(return_value=None)
        downloaded = Mock()
        download = AsyncMock(return_value=downloaded, side_effect=download_effect)
        channel = SimpleNamespace(id=789, send=AsyncMock(return_value=Mock()))
        with ExitStack() as stack:
            stack.enter_context(patch("music.player.cancel_idle_timer"))
            idle = stack.enter_context(patch("music.player.start_idle_timer", new_callable=AsyncMock))
            stack.enter_context(patch("music.player.get_cached_audio_source", cache))
            stack.enter_context(patch("music.player.get_audio_source", download))
            stack.enter_context(patch("music.player._try_audio_fallback", fallback))
            extract = stack.enter_context(patch(
                "music.player.extract_info_with_retry", return_value=info or {"url": URL},
                side_effect=extract_effect,
            ))
            hint = stack.enter_context(patch("music.player._cached_audio_fallback_hint", return_value=(None, None)))
            volume = stack.enter_context(patch("music.player.discord.PCMVolumeTransformer"))
            temp = stack.enter_context(patch(
                "music.player.get_long_audio_source", new=AsyncMock(side_effect=temp_effect)
            ))
            ffmpeg = stack.enter_context(patch("music.player.discord.FFmpegPCMAudio", return_value=Mock()))
            loading = stack.enter_context(patch("music.player.create_loading_panel"))
            stack.enter_context(patch("music.player.MusicControl"))
            stack.enter_context(patch("music.player.create_radio_panel"))
            stack.enter_context(patch("music.player.edit_panel_message", new_callable=AsyncMock))
            stack.enter_context(patch("music.player._record_recent_safely", new=AsyncMock(side_effect=record)))
            preloader = stack.enter_context(patch("music.player.preload_audio", new_callable=AsyncMock))
            if preload:
                await preload_track(song)
            await _play_next_locked(bot, vc, channel, state)
            await asyncio.sleep(0)
        return SimpleNamespace(vc=vc, cache=cache, fallback=fallback, download=download,
                               extract=extract, hint=hint, volume=volume, loading=loading, temp=temp,
                               preloader=preloader, ffmpeg=ffmpeg, channel=channel, state=state, idle=idle)

    def spotify_search_item(self):
        return {"source": "spotify", "url": "spotify:track:" + "A1" * 11,
                "title": "Undress Rehearsal - Hardtekk", "duration": 180,
                "search_query": "Undress Rehearsal - Hardtekk Artist audio"}

    async def test_empty_spotify_search_notifies_channel_and_advances_or_ends_queue(self):
        next_song = {"source": "youtube", "url": URL, "title": "Next song", "duration": 180}
        for following in ((next_song,), ()):
            with self.subTest(has_next=bool(following)), patch("music.player.cached_search_match", return_value=None):
                song = self.spotify_search_item()
                with self.assertLogs("music.player", level="WARNING") as logs:
                    calls = await self.run_player(song, info={"entries": []}, following=following)
                self.assertIn("YouTube không trả về kết quả tìm kiếm", " ".join(logs.output))
                self.assertEqual([c.args[0] for c in calls.extract.call_args_list],
                                 ["ytsearch1:" + song["search_query"], "ytsearch1:" + song["title"]])
                sent = calls.channel.send.await_args_list[0].kwargs
                notice = sent["view"].children[0].children[0].content
                self.assertIn(song["title"], notice)
                self.assertIn("ĐÃ BỎ QUA", notice)
                self.assertIn("Không tìm thấy bài này trên YouTube", notice)
                self.assertFalse(sent["allowed_mentions"].everyone)
                self.assertEqual(calls.state.history, list(following))
                if following:
                    self.assertIn("Chuyển sang bài tiếp theo", notice)
                    calls.vc.play.assert_called_once()
                    calls.download.assert_awaited_once_with(URL)
                    calls.idle.assert_not_awaited()
                else:
                    self.assertIn("không còn bài để phát", notice)
                    calls.channel.send.assert_awaited_once()
                    calls.vc.play.assert_not_called()
                    calls.idle.assert_awaited_once()

    async def test_spotify_title_retry_can_play_and_missing_stream_does_not_trigger_retry(self):
        song = self.spotify_search_item()
        with patch("music.player.cached_search_match", return_value=None), patch("music.player.remember_search_match"):
            calls = await self.run_player(song, extract_effect=[
                {"entries": []}, {"entries": [{"url": "stream", "webpage_url": URL}]},
            ])
        self.assertEqual(calls.extract.call_count, 2)
        calls.vc.play.assert_called_once()
        calls.download.assert_awaited_once_with(URL)
        with patch("music.player.cached_search_match", return_value=None):
            calls = await self.run_player(self.spotify_search_item(), info={"entries": [{"id": VIDEO_ID}]})
        calls.extract.assert_called_once()
        calls.channel.send.assert_awaited_once()
        notice = calls.channel.send.await_args.kwargs["view"].children[0].children[0].content
        self.assertIn("Không lấy được nguồn phát", notice)
        self.assertNotIn("Không tìm thấy", notice)

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

    async def test_legacy_spotify_replaces_unavailable_video_on_each_playback_path(self):
        new_url = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
        info = {"url": "https://cdn.test/replacement", "webpage_url": new_url}
        for duration, use_temp in ((167, False), (700, True), (700, False)):
            with self.subTest(duration=duration, use_temp=use_temp), patch(
                "music.player.should_use_long_audio_temp", return_value=use_temp,
            ):
                song = {"title": "Song", "author": "Artist", "source": "spotify", "url": URL,
                        "search_query": "Song Artist audio", "duration": duration}
                unavailable = RuntimeError("The uploader has not made this video available in your country")
                wrapped = RuntimeError("Could not prepare long audio")
                wrapped.__cause__ = unavailable
                replacement = Mock()
                direct = duration > 600 and not use_temp
                calls = await self.run_player(
                    song, info=info, extract_effect=[unavailable, info] if direct else None,
                    download_effect=[unavailable, replacement] if duration <= 600 else None,
                    temp_effect=[wrapped, replacement] if use_temp else None,
                )
                lookups = [item.args[0] for item in calls.extract.call_args_list]
                self.assertEqual(lookups, ([URL] if direct else []) + ["ytsearch1:Song Artist audio"])
                if direct:
                    self.assertEqual(calls.ffmpeg.call_args.args[0], info["url"])
                else:
                    self.assertIs(calls.vc.play.call_args.args[0], replacement)
                    if use_temp:
                        self.assertEqual(calls.temp.await_args_list, [
                            call(URL, duration=duration), call(new_url, duration=duration),
                        ])
                    else:
                        self.assertEqual(calls.download.await_args_list, [call(URL), call(new_url)])
                calls.vc.play.assert_called_once()
                calls.fallback.assert_not_awaited()
                self.assertEqual(song["url"], URL)
                self.assertEqual(song["playback_url"], new_url)

    async def test_reopened_legacy_spotify_playlist_reuses_cached_replacement(self):
        song = {"title": "Song", "author": "Artist", "source": "spotify", "url": URL,
                "search_query": "Song Artist audio", "duration": 167}
        new_url = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
        info = {"url": "https://cdn.test/replacement", "webpage_url": new_url}
        recorded = asyncio.Event()

        async def record(*args, **kwargs):
            await _record_recent_safely(*args, **kwargs)
            recorded.set()

        async def download(url):
            if url == URL:
                raise RuntimeError("Video unavailable")
            Path(cache_manager.get_cache_paths(url)[1]).write_bytes(b"cached")
            return Mock()

        with tempfile.TemporaryDirectory() as folder, patch.object(
            music_library, "DB_PATH", str(Path(folder) / "library.db")
        ), patch.object(cache_manager, "CACHE_DIR", folder):
            await music_library.init_db()
            await music_library.create_playlist(123, 456, "Mix")
            await music_library.add_track_to_playlist(123, 456, "Mix", song)
            first, = await music_library.get_playlist_tracks(123, 456, "Mix")
            await self.run_player(first, info=info, download_effect=download, record=record)
            await asyncio.wait_for(recorded.wait(), timeout=3)

            replay, = await music_library.get_playlist_tracks(123, 456, "Mix")
            self.assertEqual(replay["url"], URL)
            self.assertNotIn("playback_url", replay)
            self.assertTrue(cache_manager.find_cached_audio_path(new_url))
            cached = Mock()
            calls = await self.run_player(replay, cached=cached)
            calls.cache.assert_awaited_once_with(new_url)
            calls.extract.assert_not_called()
            calls.download.assert_not_awaited()
            self.assertIs(calls.vc.play.call_args.args[0], cached)

    async def test_spotify_search_is_remembered_for_other_links_to_same_track(self):
        track_id = "A1" * 11
        url = "https://open.spotify.com/track/" + track_id
        song = {"title": "Song", "author": "Artist", "source": "spotify", "url": url,
                "search_query": "Song Artist audio", "duration": 180}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            music_library, "DB_PATH", str(Path(folder) / "library.db")
        ), patch.object(cache_manager, "CACHE_DIR", folder):
            first = await self.run_player(
                song, info={"url": "stream", "webpage_url": URL, "duration": 245}, preload=True,
            )
            first.extract.assert_called_once_with(
                "ytsearch1:Song Artist audio", unittest.mock.ANY, download=False,
                remember_for_download=True,
            )
            first.preloader.assert_awaited_once_with(URL)
            first.download.assert_awaited_once_with(URL)
            self.assertEqual(song["url"], url)
            self.assertEqual(song["playback_url"], URL)
            path = Path(cache_manager.get_cache_paths(URL)[1])
            path.write_bytes(b"cached")
            with patch("music.spotify.urllib.request.urlopen") as spotify:
                replay = resolve_song(url + "?si=test")
                spotify.assert_not_called()
                self.assertEqual(replay["duration"], 180)
                self.assertEqual(replay["playback_duration"], 245)
                self.assertEqual(resolve_song(SHARE_URL)["duration"], 245)
                second = await self.run_player(replay, cached=Mock())
                second.cache.assert_awaited_once_with(URL)
                second.extract.assert_not_called()
                self.assertEqual(replay["url"], url + "?si=test")
                self.assertEqual(music_library.track_key(replay), music_library.track_key(song))
                restored = {key: value for key, value in song.items() if key != "playback_url"}
                saved = await self.run_player(restored, cached=Mock())
                saved.cache.assert_awaited_once_with(URL)
                saved.extract.assert_not_called()
                path.unlink()
                spotify.return_value.read.return_value = b'<meta property="og:title" content="Refreshed">'
                self.assertEqual(resolve_song(url)["title"], "Refreshed")
                spotify.assert_called_once()

    async def test_playback_joins_spotify_preload_search_already_running(self):
        song = {"source": "spotify", "url": "spotify:track:" + "A1" * 11,
                "search_query": "Song Artist audio"}
        searching = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def extract(*args, **kwargs):
            loop.call_soon_threadsafe(searching.set)
            if not release.wait(3):
                raise TimeoutError("test did not release the Spotify search")
            return {"url": "stream", "webpage_url": URL}

        with patch("music.player.cached_search_match", return_value=None), patch(
            "music.player.remember_search_match"
        ), patch("music.player.extract_info_with_retry", side_effect=extract) as search, patch(
            "music.player.preload_audio", new_callable=AsyncMock
        ) as preload:
            preloading = asyncio.create_task(preload_track(song))
            try:
                await asyncio.wait_for(searching.wait(), 3)
                playback = asyncio.create_task(_resolve_spotify_video(song))
                await asyncio.sleep(0)
            finally:
                release.set()
            await asyncio.gather(playback, preloading)
        search.assert_called_once()
        preload.assert_awaited_once_with(URL)
        self.assertEqual(song["playback_url"], URL)

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
