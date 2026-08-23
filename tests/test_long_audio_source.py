import os
import tempfile
import time
import unittest
from unittest.mock import patch

import cache_manager


class _DummyOpusSource:
    def __init__(self):
        self.cleanup_calls = 0

    def read(self):
        return b"opus"

    def is_opus(self):
        return True

    def cleanup(self):
        self.cleanup_calls += 1


class TemporaryFileAudioSourceTests(unittest.TestCase):
    def test_cleanup_removes_file_once(self):
        descriptor, path = tempfile.mkstemp(suffix=".webm")
        os.close(descriptor)
        inner = _DummyOpusSource()
        source = cache_manager.TemporaryFileAudioSource(inner, path)

        source.cleanup()
        source.cleanup()

        self.assertFalse(os.path.exists(path))
        self.assertEqual(inner.cleanup_calls, 1)

    def test_stale_cleanup_keeps_recent_file(self):
        with tempfile.TemporaryDirectory() as directory:
            stale_path = os.path.join(directory, "stale.webm")
            recent_path = os.path.join(directory, "recent.webm")
            for path in (stale_path, recent_path):
                with open(path, "wb") as file:
                    file.write(b"audio")
            old_time = time.time() - 100
            os.utime(stale_path, (old_time, old_time))

            with patch.object(cache_manager, "LONG_AUDIO_TEMP_DIR", directory):
                cache_manager.cleanup_stale_long_audio_files(max_age=50)

            self.assertFalse(os.path.exists(stale_path))
            self.assertTrue(os.path.exists(recent_path))


class LongAudioDurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tracks_over_two_hours_are_rejected_before_download(self):
        with self.assertRaises(cache_manager.AudioDownloadError):
            await cache_manager.get_long_audio_source(
                "https://www.youtube.com/watch?v=test",
                cache_manager.LONG_AUDIO_TEMP_MAX_DURATION + 1,
            )


if __name__ == "__main__":
    unittest.main()
