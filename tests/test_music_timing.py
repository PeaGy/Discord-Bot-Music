import asyncio
from collections import deque
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from commands.play import Play, get_song_info
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

    async def test_slash_command_times_connect_metadata_and_queue_ui_without_extra_lookup(self):
        interaction, state, vc = self.make_interaction()
        song = {"title": SECRET, "duration": 180, "url": URL, "source": "youtube"}
        logger = Mock()
        with patch("commands.play.logger", logger), patch(
            "commands.play.get_song_info", return_value=song
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
            "commands.play.get_song_info", side_effect=RuntimeError(SECRET)
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
                "commands.play.find_cached_audio_path", return_value=None
            ), patch("commands.play.audio_fallback_enabled", return_value=False), patch(
                "commands.play.youtube_ydl_options", side_effect=lambda options: options
            ), patch("commands.play.extract_info_with_retry", return_value={
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
