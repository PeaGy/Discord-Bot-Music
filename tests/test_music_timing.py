import asyncio
from collections import deque
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from commands.play import DuplicateConfirmView, Play
from commands.search import SearchSelect
from music.resolver import get_song_info
from music.player import _play_next_locked, play_next
from music.timing import phase_timer


VIDEO_ID = "mRD0-GxqHVo"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
SECRET = "private-query-and-proxy-password"


def timing_messages(logger):
    return [
        call.args[0] % call.args[1:]
        for call in logger.info.call_args_list
        if call.args and str(call.args[0]).startswith("music-timing ")
    ]


class MusicTimingHelperTests(unittest.TestCase):
    def test_phase_measures_elapsed_and_safe_correlation_ids(self):
        logger = Mock()
        with patch("music.timing.perf_counter", side_effect=[10.0, 11.25]):
            with phase_timer(logger, "download", guild_id=123, track_id=VIDEO_ID):
                pass
        message, = timing_messages(logger)
        self.assertIn("phase=download elapsed=1.250s outcome=success", message)
        self.assertIn(f"guild=123 track={VIDEO_ID}", message)

    def test_errors_and_cancellation_propagate_without_logging_exception_text(self):
        for error, outcome in [(RuntimeError(SECRET), "error"),
                               (asyncio.CancelledError(SECRET), "cancelled")]:
            with self.subTest(outcome=outcome):
                logger = Mock()
                with self.assertRaises(type(error)) as raised:
                    with phase_timer(logger, "metadata-wait", guild_id=123):
                        raise error
                self.assertIs(raised.exception, error)
                message, = timing_messages(logger)
                self.assertIn(f"outcome={outcome}", message)
                self.assertNotIn(SECRET, message)

    def test_urls_and_unbounded_labels_are_not_accepted_as_correlation_fields(self):
        logger = Mock()
        private_url = f"https://user:{SECRET}@proxy.test:1234/signed?token={SECRET}"
        with phase_timer(logger, private_url, guild_id=private_url, track_id=private_url):
            pass
        message, = timing_messages(logger)
        self.assertIn("phase=-", message)
        self.assertIn("guild=- track=-", message)
        self.assertNotIn(SECRET, message)
        self.assertNotIn("proxy.test", message)


class PlayTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_starts_while_voice_is_connecting(self):
        interaction, state, vc = self.make_interaction()
        loop = asyncio.get_running_loop()
        started = asyncio.Event()

        def lookup(_query):
            loop.call_soon_threadsafe(started.set)
            return {"title": "Track", "duration": 10, "url": URL}

        async def connect(**kwargs):
            await asyncio.wait_for(started.wait(), timeout=3)
            return vc

        interaction.user.voice.channel.connect.side_effect = connect
        with patch("commands.play.resolve_song", side_effect=lookup), patch(
            "commands.play.get_guild_state", return_value=state
        ), patch("commands.play.play_next", new=AsyncMock()):
            await Play(Mock())._play(interaction, URL, front=False)
        self.assertEqual(len(state.queue), 1)

    def make_interaction(self):
        guild = SimpleNamespace(id=123, voice_client=None)
        vc = Mock()
        vc.guild = guild
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        voice_channel = SimpleNamespace(connect=AsyncMock(return_value=vc))
        vc.channel = voice_channel
        user = SimpleNamespace(id=456, voice=SimpleNamespace(channel=voice_channel),
                               display_avatar=SimpleNamespace(url="https://avatar.test/image"))
        interaction = SimpleNamespace(guild=guild, user=user, channel=Mock(),
                                      response=SimpleNamespace(defer=AsyncMock()),
                                      followup=SimpleNamespace(send=AsyncMock()))
        state = SimpleNamespace(queue=deque(), history=[])
        return interaction, state, vc

    async def test_spotify_queues_preload_for_the_same_item_and_rejects_repeat(self):
        interaction, state, vc = self.make_interaction()
        interaction.guild.voice_client = vc
        vc.is_playing.return_value = True
        track_id = "a1" * 11
        url = "https://open.spotify.com/track/" + track_id
        song = {"title": "Song", "duration": 180, "source": "spotify", "url": url}
        with patch("commands.play.resolve_song", return_value=song) as resolve, patch(
            "commands.play.get_guild_state", return_value=state
        ), patch("commands.play.preload_track", new_callable=AsyncMock) as preload, patch(
            "commands.play.DuplicateConfirmView"
        ) as duplicate:
            play = Play(Mock())
            await play._play(interaction, url, front=False)
            await asyncio.sleep(0)
            item = state.queue.popleft()
            self.assertIs(preload.await_args.args[0], item)
            item["playback_url"] = URL
            state.history.append(item)

            uri = "spotify:track:" + track_id
            resolve.return_value = {**song, "url": uri, "playback_url": URL}
            await play._play(interaction, uri, front=False)
        duplicate.assert_called_once()
        self.assertFalse(state.queue)

    async def test_play_last_track_again_after_queue_ends(self):
        interaction, state, vc = self.make_interaction()
        interaction.guild.voice_client = vc
        song = {"title": "Nevada", "duration": 180, "url": URL}
        state.history.append(song)
        with patch("commands.play.resolve_song", return_value=song), patch(
            "commands.play.get_guild_state", return_value=state
        ), patch("commands.play.play_next", new_callable=AsyncMock) as play_next_mock:
            await Play(Mock())._play(interaction, URL, front=False)
        play_next_mock.assert_awaited_once()
        self.assertEqual(state.queue[0]["url"], URL)
        self.assertEqual(state.history, [song])
        self.assertNotIn("view", interaction.followup.send.await_args.kwargs)

    async def test_duplicate_warning_distinguishes_paused_and_queued_tracks(self):
        song = {"title": "Nevada", "duration": 180, "url": URL}
        for paused, expected in ((True, "đang tạm dừng"), (False, "đã ở vị trí #2")):
            with self.subTest(paused=paused):
                interaction, state, vc = self.make_interaction()
                interaction.guild.voice_client = vc
                vc.is_paused.return_value = paused
                state.history.append(song)
                state.queue.extend([{"url": "other"}, song])
                with patch("commands.play.get_guild_state", return_value=state), patch(
                    "commands.play.resolve_song", return_value=song
                ):
                    await Play(Mock())._play(interaction, URL, front=False)
                warning = interaction.followup.send.await_args
                self.assertIn(expected, warning.args[0])
                self.assertIsInstance(warning.kwargs["view"], DuplicateConfirmView)
                self.assertEqual(len(state.queue), 2)

    async def test_playnext_preloads_new_head_even_when_queue_already_has_tracks(self):
        interaction, state, vc = self.make_interaction()
        interaction.guild.voice_client = vc
        vc.is_playing.return_value = True
        previous = {"title": "Waiting", "url": URL}
        state.queue.append(previous)
        url = "https://open.spotify.com/track/" + "a1" * 11
        song = {"title": "Song", "duration": 180, "source": "spotify", "url": url}
        interaction.response.edit_message = AsyncMock()
        with patch("commands.play.resolve_song", return_value=song), patch(
            "commands.play.get_guild_state", return_value=state
        ), patch("commands.play.preload_track", new_callable=AsyncMock) as preload:
            play = Play(Mock())
            await play._play(interaction, url, front=True)
            await asyncio.sleep(0)
            first = state.queue[0]
            self.assertIs(preload.await_args.args[0], first)
            self.assertIs(state.queue[1], previous)

            preload.reset_mock()
            await play._play(interaction, url, front=True)
            view = interaction.followup.send.await_args.kwargs["view"]
            self.assertIsInstance(view, DuplicateConfirmView)
            await view.confirm.callback(interaction)
            await asyncio.sleep(0)
            self.assertIs(preload.await_args.args[0], state.queue[0])
            self.assertIs(state.queue[1], first)
            preload.assert_awaited_once()

    async def test_playnext_rechecks_queue_head_after_preload_delay(self):
        for add_with_search in (False, True):
            with self.subTest(add_with_search=add_with_search):
                await self.check_playnext_preload_order(add_with_search)

    async def check_playnext_preload_order(self, add_with_search):
        interaction, state, vc = self.make_interaction()
        interaction.guild.voice_client = vc
        vc.is_playing.return_value = True
        interaction.message = SimpleNamespace(delete=AsyncMock())
        release_delay = asyncio.Event()
        preload_started = asyncio.Event()
        other_url = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
        songs = [
            {"title": title, "duration": 180, "source": "youtube", "url": url}
            for title, url in (("A", URL), ("B", other_url))
        ]

        async def wait_for_release(_):
            await release_delay.wait()

        lookups = songs[1:] if add_with_search else songs
        with patch("commands.play.resolve_song", side_effect=lookups), patch(
            "commands.play.get_guild_state", return_value=state
        ), patch(
            "music.state.get_guild_state", return_value=state
        ), patch("music.player.asyncio.sleep", new=AsyncMock(side_effect=wait_for_release)), patch(
            "music.player.preload_audio", new=AsyncMock(side_effect=lambda _: preload_started.set())
        ) as preload:
            play = Play(Mock())
            if add_with_search:
                select = SearchSelect([{**songs[0], "webpage_url": URL}], interaction)
                select._values = ["0"]
                await select.callback(interaction)
            else:
                await play._play(interaction, URL, front=True)
            await play._play(interaction, other_url, front=True)
            release_delay.set()
            await asyncio.wait_for(preload_started.wait(), timeout=3)

        self.assertEqual([item["title"] for item in state.queue], ["B", "A"])
        preload.assert_awaited_once_with(other_url)

    async def test_slash_command_times_connect_metadata_and_queue_ui_without_extra_lookup(self):
        interaction, state, vc = self.make_interaction()
        song = {"title": SECRET, "duration": 180, "url": URL, "source": "youtube"}
        logger = Mock()
        with patch("commands.play.logger", logger), patch(
            "commands.play.resolve_song", return_value=song
        ) as metadata, patch("commands.play.get_guild_state", return_value=state), patch(
            "commands.play.play_next", new=AsyncMock()
        ) as next_track:
            await Play(Mock())._play(interaction, SECRET, front=False)
        phases = "\n".join(timing_messages(logger))
        for phase in ("interaction-defer", "voice-connect", "metadata-wait", "queue-ui"):
            self.assertIn(f"phase={phase} ", phases)
        self.assertNotIn(SECRET, phases)
        metadata.assert_called_once_with(SECRET)
        interaction.user.voice.channel.connect.assert_awaited_once_with(self_deaf=True)
        next_track.assert_awaited_once()
        self.assertEqual(state.queue[0]["title"], SECRET)

    async def test_metadata_error_is_timed_and_does_not_reach_queue(self):
        interaction, state, vc = self.make_interaction()
        interaction.guild.voice_client = vc
        logger = Mock()
        with patch("commands.play.logger", logger), patch(
            "commands.play.resolve_song", side_effect=RuntimeError(SECRET)
        ), patch("commands.play.get_guild_state", return_value=state), patch(
            "commands.play.start_idle_timer", new=AsyncMock()
        ), patch("commands.play.play_next", new=AsyncMock()) as next_track:
            await Play(Mock())._play(interaction, SECRET, front=False)
        # Existing warning copy is outside this diagnostic contract. New timing
        # events must contain neither the input query nor raw upstream errors.
        phases = "\n".join(timing_messages(logger))
        self.assertIn("phase=metadata-wait", phases)
        self.assertIn("outcome=error", phases)
        self.assertNotIn("phase=voice-connect", phases)
        self.assertNotIn("phase=queue-ui", phases)
        self.assertNotIn(SECRET, phases)
        self.assertFalse(state.queue)
        next_track.assert_not_awaited()

    def test_only_youtube_metadata_opts_into_download_handoff(self):
        for query, expected in [(URL, True), ("Artist song", True),
                                ("https://soundcloud.com/artist/song", False)]:
            with self.subTest(query=query), patch(
                "music.resolver.find_cached_audio_path", return_value=None
            ), patch("music.resolver.audio_fallback_enabled", return_value=False), patch(
                "music.resolver.youtube_ydl_options", side_effect=lambda options: options
            ), patch("music.resolver.extract_info_with_retry", return_value={
                "title": "Track", "duration": 180, "webpage_url": URL,
                "formats": [{"url": f"https://cdn.test/{SECRET}", "acodec": "opus"}],
            }) as extract:
                song = get_song_info(query)
            self.assertIs(extract.call_args.kwargs["remember_for_download"], expected)
            self.assertNotIn("formats", song)
            self.assertNotIn(SECRET, str(song))


class PlayerTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_lock_wait_logged_before_preparing_source(self):
        state = SimpleNamespace(play_lock=asyncio.Lock())
        vc = Mock()
        vc.guild = SimpleNamespace(id=123)
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        logger = Mock()

        async def prepare(*args):
            self.assertTrue(state.play_lock.locked())
            self.assertIn("phase=play-lock-wait", timing_messages(logger)[0])

        with patch("music.player.logger", logger), patch(
            "music.player.get_guild_state", return_value=state
        ), patch("music.player._play_next_locked", new=AsyncMock(side_effect=prepare)) as locked:
            await play_next(Mock(), vc, Mock())
        locked.assert_awaited_once()
        self.assertFalse(state.play_lock.locked())

    async def test_source_ready_and_voice_submission_are_distinct_without_audio_reads(self):
        song = {"title": SECRET, "duration": 180, "url": URL, "source": "youtube"}
        state = SimpleNamespace(queue=deque([song]), history=[], text_channel=None,
                                now_playing_message=None, loop_mode="off", autoplay=False)
        vc = Mock()
        vc.guild = SimpleNamespace(id=123)
        vc.is_connected.return_value = True
        bot = SimpleNamespace(loop=asyncio.get_running_loop(), user=Mock())
        source = Mock()
        logger = Mock()
        with ExitStack() as stack:
            stack.enter_context(patch("music.player.logger", logger))
            stack.enter_context(patch("music.player.cancel_idle_timer"))
            stack.enter_context(patch("music.player.should_use_long_audio_temp", return_value=False))
            stack.enter_context(patch("music.player.get_cached_audio_source", new=AsyncMock(return_value=None)))
            download = stack.enter_context(patch("music.player.get_audio_source", new=AsyncMock(return_value=source)))
            stack.enter_context(patch("music.player._cached_audio_fallback_hint", return_value=(None, None)))
            extract = stack.enter_context(patch("music.player.extract_info_with_retry"))
            stack.enter_context(patch("music.player.create_loading_panel", return_value=Mock()))
            stack.enter_context(patch("music.player.MusicControl", return_value=Mock()))
            stack.enter_context(patch("music.player.send_panel_message", new=AsyncMock(return_value=Mock())))
            stack.enter_context(patch("music.player.edit_panel_message", new=AsyncMock()))
            stack.enter_context(patch("music.player._record_recent_safely", new=AsyncMock()))
            volume = stack.enter_context(patch("music.player.discord.PCMVolumeTransformer"))
            await _play_next_locked(bot, vc, Mock(), state)
            await asyncio.sleep(0)
        phases = "\n".join(timing_messages(logger))
        self.assertIn("phase=loading-ui", phases)
        self.assertIn("phase=source-prepare", phases)
        self.assertIn("phase=voice-submit", phases)
        self.assertLess(phases.index("phase=source-prepare"), phases.index("phase=voice-submit"))
        self.assertIn(f"guild=123 track={VIDEO_ID}", phases)
        self.assertNotIn(SECRET, phases)
        self.assertNotIn("https://", phases)
        download.assert_awaited_once_with(URL)
        extract.assert_not_called()
        volume.assert_not_called()
        source.read.assert_not_called()
        self.assertIs(vc.play.call_args.args[0], source)


if __name__ == "__main__":
    unittest.main()
