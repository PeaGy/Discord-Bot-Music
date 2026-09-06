import asyncio
from collections import deque
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from music.player import (
    _needs_stream_lookup,
    _play_next_locked,
    _youtube_entry_url,
)


class MusicPlayerRouteTests(unittest.TestCase):
    def test_cached_youtube_page_does_not_resolve_unused_stream(self):
        song = {
            "source": "youtube",
            "url": "https://www.youtube.com/watch?v=mRD0-GxqHVo",
        }
        self.assertFalse(_needs_stream_lookup(song, use_direct_stream=False))

    def test_spotify_and_direct_streams_still_resolve(self):
        spotify = {"source": "spotify", "url": "https://open.spotify.com/track/test"}
        youtube = {
            "source": "youtube",
            "url": "https://www.youtube.com/watch?v=mRD0-GxqHVo",
        }
        self.assertTrue(_needs_stream_lookup(spotify, use_direct_stream=False))
        self.assertTrue(_needs_stream_lookup(youtube, use_direct_stream=True))

    def test_autoplay_flat_entry_becomes_canonical_watch_url(self):
        self.assertEqual(
            _youtube_entry_url({"id": "mRD0-GxqHVo", "url": "mRD0-GxqHVo"}),
            "https://www.youtube.com/watch?v=mRD0-GxqHVo",
        )


class EarlyAudioFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("music.player.get_cached_audio_source", new=AsyncMock(return_value=None))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_failed_initial_metadata_streams_fallback_without_cache(self):
        song = {
            "title": "Heat Waves",
            "author": "Glass Animals",
            "duration": 238,
            "url": "https://www.youtube.com/watch?v=mRD0-GxqHVo",
            "source": "youtube",
            "youtube_metadata_failed": True,
            "requester": None,
        }
        resolved = {
            "stream_url": "https://cf-media.sndcdn.com/audio.mp3",
            "webpage_url": "https://soundcloud.com/glassanimals/heat-waves",
        }
        state = SimpleNamespace(
            queue=deque([song]),
            history=[],
            text_channel=None,
            now_playing_message=None,
            loop_mode="off",
            autoplay=False,
        )
        guild = SimpleNamespace(id=123)

        class DummyVoiceClient:
            current_volume = 1.0

            def __init__(self):
                self.guild = guild
                self.played = None

            def is_connected(self):
                return True

            def is_playing(self):
                return False

            def is_paused(self):
                return False

            def play(self, source, *, after):
                self.played = (source, after)

        vc = DummyVoiceClient()
        bot = SimpleNamespace(loop=asyncio.get_running_loop(), user=Mock())
        base_source = Mock()
        resolved["audio_source"] = base_source
        volume_source = Mock()

        with patch("music.player.cancel_idle_timer"):
            with patch(
                "music.player.get_cached_soundcloud_page",
                return_value=None,
            ):
                with patch(
                    "music.player._try_audio_fallback",
                    new=AsyncMock(return_value=resolved),
                ) as fallback:
                    with patch(
                        "music.player.get_audio_source",
                        new=AsyncMock(),
                    ) as cached_source:
                        with patch(
                            "music.player.discord.FFmpegPCMAudio",
                            return_value=base_source,
                        ) as ffmpeg:
                            with patch(
                                "music.player.discord.PCMVolumeTransformer",
                                return_value=volume_source,
                            ):
                                with patch(
                                    "music.player.MusicControl",
                                    return_value=Mock(),
                                ):
                                    with patch(
                                        "music.player.send_panel_message",
                                        new=AsyncMock(return_value=Mock()),
                                    ):
                                        with patch(
                                            "music.player._record_recent_safely",
                                            new=AsyncMock(),
                                        ):
                                            await _play_next_locked(
                                                bot,
                                                vc,
                                                Mock(),
                                                state,
                                            )
                                            await asyncio.sleep(0)

        fallback.assert_awaited_once_with(song)
        cached_source.assert_not_awaited()
        # Playback must reuse the primed, validated process (not open a new URL).
        ffmpeg.assert_not_called()
        self.assertIs(vc.played[0], volume_source)

    async def test_failed_early_fallback_is_not_repeated_after_youtube_download(self):
        song = {
            "title": "Heat Waves",
            "author": "Glass Animals",
            "duration": 0,
            "url": "https://www.youtube.com/watch?v=mRD0-GxqHVo",
            "source": "youtube",
            "youtube_metadata_failed": True,
            "requester": None,
        }
        state = SimpleNamespace(
            queue=deque([song]),
            history=[],
            text_channel=None,
            now_playing_message=None,
            loop_mode="off",
            autoplay=False,
        )
        guild = SimpleNamespace(id=123)

        class DummyVoiceClient:
            def __init__(self):
                self.guild = guild

            def is_connected(self):
                return True

            def is_playing(self):
                return False

            def is_paused(self):
                return False

        fallback = AsyncMock(return_value=None)
        with patch("music.player.cancel_idle_timer"):
            with patch("music.player._try_audio_fallback", fallback):
                with patch(
                    "music.player.get_audio_source",
                    new=AsyncMock(
                        side_effect=RuntimeError(
                            "Sign in to confirm you're not a bot"
                        )
                    ),
                ):
                    with patch(
                        "music.player.send_panel_message",
                        new=AsyncMock(return_value=None),
                    ):
                        with patch(
                            "music.player.start_idle_timer",
                            new=AsyncMock(),
                        ):
                            await _play_next_locked(
                                SimpleNamespace(
                                    loop=asyncio.get_running_loop(),
                                    user=Mock(),
                                ),
                                DummyVoiceClient(),
                                Mock(),
                                state,
                            )

        fallback.assert_awaited_once_with(song)


if __name__ == "__main__":
    unittest.main()
