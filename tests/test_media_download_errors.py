import tempfile
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

from features import media_downloader as media


VIDEO = media.MediaItem("youtube", "https://youtu.be/mRD0-GxqHVo?si=private-share",
                       "Title", "Artist", 180, None, format_id="137+140", youtube_audio_format_id="140")
PROXY = "socks5://test-user:test-pass@private-proxy.test:4000"
LIMIT = 10 * 1024 * 1024


class DownloadErrors(unittest.TestCase):
    def test_ytdlp_warnings_and_public_messages(self):
        generic = "Nguồn tải từ nền tảng đang từ chối hoặc file vượt giới hạn dung lượng."
        cases = [
            ("mp4", "max_filesize exceeded", "file_too_large", "File vượt giới hạn dung lượng."),
            ("mp4", "File is larger than max-filesize", "file_too_large", "File vượt giới hạn dung lượng."),
            ("mp3", "Sign in to confirm you're not a bot", "youtube_blocked", "YouTube đang chặn hoặc yêu cầu xác minh."),
            ("mp4", "HTTP Error 403: Forbidden", "youtube_blocked", "YouTube đang chặn hoặc yêu cầu xác minh."),
            ("mp3", "SOCKS5 proxy connection refused", "proxy_connection", "Không kết nối được proxy."),
            ("mp4", "Requested format is not available", "format_unavailable", generic),
            ("mp4", "Unknown extractor failure", "other", generic),
        ]
        for fmt, detail, reason, public in cases:
            with self.subTest(detail=detail), patch.object(media, "_common_ydl_options", return_value={
                "proxy": PROXY, "logger": media._YTDLPLogger()
            }), patch.object(media.yt_dlp, "YoutubeDL") as ydl, tempfile.TemporaryDirectory() as directory:
                raw = f"{detail}; {PROXY} https://cdn.test/file?signature=secret-sign Cookie: sid=private-cookie"
                error = media.yt_dlp.utils.DownloadError(raw)
                ydl.return_value.__enter__.return_value.extract_info.side_effect = error
                with self.assertLogs(media.logger, "WARNING") as logs, self.assertRaises(media.MediaDownloadError) as caught:
                    media._download_media_sync(replace(VIDEO, output_format=fmt), LIMIT, directory)
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(str(caught.exception), public)
                warning, = logs.output
                for field in (detail, "platform=youtube", "media_kind=video", f"output_format={fmt}",
                              "format_id=" + ("140" if fmt == "mp3" else "137+140"), "duration=180",
                              f"upload_limit={LIMIT}", "max_filesize=" + (str(LIMIT - media.UPLOAD_SAFETY_MARGIN)
                              if fmt == "mp4" else "None"), "proxy=True", "media_ref=mRD0-GxqHVo", f"reason={reason}"):
                    self.assertIn(field, warning)
                for secret in (PROXY, "private-proxy", "test-pass", "private-share", "private-cookie", "secret-sign"):
                    self.assertNotIn(secret, warning)

    def test_debug_and_warning_details_are_redacted_before_truncation(self):
        for payload in ("Authorization: Bearer private-value", "YTDLP_PROXY=" + PROXY,
                        "{'Cookie': 'SID=private-value'}", "token=private-value",
                        "https://cdn.test/private-value", "broken\nprivate-value"):
            with self.subTest(payload=payload), self.assertLogs(media.logger, "DEBUG") as logs:
                media._YTDLPLogger().error("failure " + payload + " " + "x" * 600)
            self.assertNotIn("private-value", logs.output[0])
            self.assertNotIn(PROXY, logs.output[0])
        self.assertEqual(len(media._safe_error_detail("x" * 600)), 500)

    def test_size_abort_without_download_error(self):
        options = {"logger": media._YTDLPLogger()}
        def abort(*args, **kwargs):
            options["logger"].debug("[download] File is larger than max-filesize. Aborting.")
            return {"id": "mRD0-GxqHVo"}
        with patch.object(media, "_common_ydl_options", return_value=options), patch.object(
            media.yt_dlp, "YoutubeDL"
        ) as ydl, tempfile.TemporaryDirectory() as directory:
            ydl.return_value.__enter__.return_value.extract_info.side_effect = abort
            with self.assertLogs(media.logger, "WARNING"), self.assertRaises(media.MediaDownloadError) as caught:
                media._download_media_sync(replace(VIDEO, output_format="mp4"), LIMIT, directory)
        self.assertEqual(caught.exception.reason, "file_too_large")

    def test_x_animation_logs_original_cdn_error(self):
        item = replace(VIDEO, platform="x", url="https://x.com/artist/status/12345?token=private-value",
                       media_kind="gif", output_format="gif", format_id=None, youtube_audio_format_id=None,
                       direct_url="https://video.twimg.com/clip.mp4?token=private-value")
        with patch.object(media, "getproxies", return_value={}), \
             patch.object(media, "urlopen", side_effect=OSError("connection reset " + item.direct_url)), \
             self.assertLogs(media.logger, "WARNING") as logs, self.assertRaises(media.MediaDownloadError):
            media._download_media_sync(item, LIMIT, ".")
        for field in ("connection reset", "platform=x", "media_kind=gif", "proxy=False", "status/12345"):
            self.assertIn(field, logs.output[0])
        self.assertNotIn("private-value", logs.output[0])

    def test_probe_and_unexpected_extractor_exception_keep_safe_context(self):
        options = {"proxy": PROXY, "logger": media._YTDLPLogger()}
        with patch.object(media, "_common_ydl_options", return_value=options), patch.object(
            media.yt_dlp, "YoutubeDL"
        ) as ydl:
            ydl.return_value.__enter__.return_value.extract_info.side_effect = RuntimeError(
                "HTTP Error 403: Forbidden " + PROXY
            )
            with self.assertLogs(media.logger, "WARNING") as logs, self.assertRaises(media.MediaDownloadError) as caught:
                media._probe_media_sync("youtube", VIDEO.url, LIMIT, "mp3")
        self.assertEqual(caught.exception.reason, "youtube_blocked")
        self.assertIn("HTTP Error 403: Forbidden", logs.output[0])
        self.assertIn("proxy=True", logs.output[0])
        self.assertNotIn("private-proxy", logs.output[0])


class DownloadButtonErrors(unittest.IsolatedAsyncioTestCase):
    async def test_button_logs_machine_reason_and_returns_short_message(self):
        cog = media.MediaDownloader(Mock())
        cog.bot.get_cog.return_value = None
        view = media.MediaDownloadView(cog, VIDEO)
        interaction = Mock(filesize_limit=LIMIT)
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        message = "Không kết nối được proxy."
        with patch.object(media, "_download_media_sync", side_effect=media.MediaDownloadError(
            message, reason="proxy_connection"
        )), patch.object(view, "_run_progress_tracker", new_callable=AsyncMock), self.assertLogs(media.logger, "WARNING") as logs:
            await view.download(interaction)
        self.assertIn("Nút tải media bị từ chối", logs.output[0])
        self.assertIn("reason=proxy_connection", logs.output[0])
        interaction.edit_original_response.assert_awaited_once_with(content="❌ " + message, attachments=[])
