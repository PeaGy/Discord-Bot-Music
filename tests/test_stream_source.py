import asyncio
import shlex
import threading
import unittest
from unittest.mock import Mock, patch

from music.player import FFMPEG_OPTIONS
from music.stream_source import finite_stream_options, open_fallback_stream


class FiniteStreamOptionsTests(unittest.TestCase):
    def test_eof_reconnect_is_only_retained_for_existing_radio_path(self):
        options = finite_stream_options()
        self.assertNotIn("-reconnect_at_eof", options["before_options"])
        self.assertIn("-rw_timeout 8000000", options["before_options"])
        self.assertIn("-reconnect_at_eof 1", FFMPEG_OPTIONS["before_options"])
        self.assertEqual(options["options"], "-vn")

    def test_headers_are_preserved_without_command_or_header_injection(self):
        options = finite_stream_options({
            "User-Agent": "Peto's Player/1.0",
            "Referer": "https://soundcloud.com/",
            "Origin": "https://soundcloud.com\r\nInjected: bad",
            "Authorization": "private-value",
            "Cookie": "private-cookie",
        })
        args = shlex.split(options["before_options"])
        headers = args[args.index("-headers") + 1]
        self.assertEqual(headers, "User-Agent: Peto's Player/1.0\r\nReferer: https://soundcloud.com/\r\n")
        self.assertNotIn("private", headers)
        self.assertNotIn("Injected", headers)


class StreamStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_frame_is_replayed_and_read_runs_off_event_loop(self):
        source = Mock()
        main_thread = threading.get_ident()
        reads = []
        frames = iter([b"a" * 3840, b"b" * 3840, b""])

        def read():
            reads.append(threading.get_ident())
            return next(frames)

        source.read.side_effect = read
        with patch("music.stream_source.discord.FFmpegPCMAudio", return_value=source) as ffmpeg:
            primed = await open_fallback_stream({"stream_url": "https://cdn.test/track"})
        self.assertNotEqual(reads[0], main_thread)
        ffmpeg.assert_called_once()
        self.assertFalse(primed.is_opus())
        self.assertEqual(primed.read(), b"a" * 3840)
        self.assertEqual(len(reads), 1)
        self.assertEqual(primed.read(), b"b" * 3840)
        self.assertEqual(primed.read(), b"")
        primed.cleanup()
        primed.cleanup()
        source.cleanup.assert_called_once()

    async def test_immediate_eof_closes_source(self):
        source = Mock()
        source.read.return_value = b""
        with patch("music.stream_source.discord.FFmpegPCMAudio", return_value=source):
            with self.assertRaisesRegex(RuntimeError, "frame audio"):
                await open_fallback_stream({"stream_url": "https://cdn.test/empty"})
        source.cleanup.assert_called_once()

    async def test_timeout_kills_stalled_reader_and_does_not_block_loop(self):
        released = threading.Event()
        source = Mock()
        source.read.side_effect = lambda: (released.wait(2), b"")[1]
        source.cleanup.side_effect = released.set
        with patch("music.stream_source.discord.FFmpegPCMAudio", return_value=source):
            with self.assertRaises(TimeoutError):
                await open_fallback_stream({"stream_url": "https://cdn.test/stall"}, timeout=0.05)
        self.assertTrue(released.is_set())
        source.cleanup.assert_called_once()

    async def test_cancellation_closes_source_while_first_frame_is_pending(self):
        started, released = threading.Event(), threading.Event()
        source = Mock()

        def read():
            started.set()
            released.wait(2)
            return b""

        source.read.side_effect = read
        source.cleanup.side_effect = released.set
        with patch("music.stream_source.discord.FFmpegPCMAudio", return_value=source):
            task = asyncio.create_task(open_fallback_stream({"stream_url": "https://cdn.test/stall"}))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(released.is_set())
        source.cleanup.assert_called_once()

    async def test_timeout_during_process_creation_cleans_up_late_process(self):
        started, release_constructor, cleaned = threading.Event(), threading.Event(), threading.Event()
        source = Mock()
        source.cleanup.side_effect = cleaned.set

        def construct(*args, **kwargs):
            started.set()
            release_constructor.wait(2)
            return source

        with patch("music.stream_source.discord.FFmpegPCMAudio", side_effect=construct):
            task = asyncio.create_task(open_fallback_stream({"stream_url": "https://cdn.test/stall"}, timeout=0.05))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            with self.assertRaises(TimeoutError):
                await task
            release_constructor.set()
            self.assertTrue(await asyncio.to_thread(cleaned.wait, 2))
        source.read.assert_not_called()
        source.cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
