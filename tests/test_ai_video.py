import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from features.ai_chat import (
    GrokChat,
    _extract_video_assets,
    _run_video_tool,
    _video_ffmpeg_executable,
)


class VideoUnderstandingTests(unittest.TestCase):
    def test_video_attachment_detection(self) -> None:
        mp4 = SimpleNamespace(content_type="video/mp4", filename="clip.mp4")
        mov = SimpleNamespace(content_type=None, filename="camera.MOV")
        image = SimpleNamespace(content_type="image/png", filename="frame.png")

        self.assertTrue(GrokChat._is_video_attachment(mp4))
        self.assertTrue(GrokChat._is_video_attachment(mov))
        self.assertFalse(GrokChat._is_video_attachment(image))

    def test_video_analysis_requires_an_explicit_request(self) -> None:
        self.assertTrue(
            GrokChat._looks_like_video_request("video này nói gì?", direct_video=True)
        )
        self.assertTrue(
            GrokChat._looks_like_video_request("nó nói gì vậy?", direct_video=False)
        )
        self.assertTrue(GrokChat._looks_like_video_request("", direct_video=True))
        self.assertFalse(
            GrokChat._looks_like_video_request("clip này vui ghê", direct_video=True)
        )

    def test_ffmpeg_extracts_ordered_frames_and_audio(self) -> None:
        executable = _video_ffmpeg_executable()
        if not Path(executable).is_file() and executable == "ffmpeg":
            self.skipTest("FFmpeg không có trên máy kiểm thử")

        with tempfile.TemporaryDirectory(prefix="peto-video-test-") as directory:
            source = os.path.join(directory, "sample.mp4")
            _run_video_tool([
                "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", source,
            ])

            duration, frames, audio_path = _extract_video_assets(
                source,
                directory,
                6,
            )

            self.assertGreater(duration, 1.8)
            self.assertLess(duration, 2.2)
            self.assertGreaterEqual(len(frames), 4)
            self.assertLessEqual(len(frames), 6)
            self.assertEqual([time for _, time in frames], sorted(time for _, time in frames))
            self.assertTrue(all(Path(path).is_file() for path, _ in frames))
            self.assertIsNotNone(audio_path)
            assert audio_path is not None
            self.assertGreater(os.path.getsize(audio_path), 0)


if __name__ == "__main__":
    unittest.main()
