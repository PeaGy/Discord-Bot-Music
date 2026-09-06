import contextlib
import io
import unittest
from unittest.mock import patch

from scripts.manual import check_youtube_ipv4 as probe


class YoutubeIpv4ProbeTests(unittest.TestCase):
    def test_probe_disables_proxy_and_cookies_and_does_not_download(self):
        base = {
            "proxy": "socks5://127.0.0.1:40000",
            "cookiefile": "unused-cookies.txt",
            "cookiesfrombrowser": ("firefox",),
            "extractor_args": {"youtubepot-bgutilhttp": {"base_url": ["http://[::1]:4416"]}},
        }
        info = {
            "title": "Test track",
            "formats": [{"url": "https://example.invalid/audio", "acodec": "opus"}],
        }
        with (
            patch.object(probe, "youtube_ydl_options", return_value=base),
            patch.object(probe, "extract_info_with_retry", return_value=info) as extract,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(probe.probe(), 0)

        options = extract.call_args.args[1]
        self.assertEqual(options["proxy"], "")
        self.assertEqual(options["source_address"], "0.0.0.0")
        self.assertIsNone(options["cookiefile"])
        self.assertIsNone(options["cookiesfrombrowser"])
        self.assertIn("youtubepot-bgutilhttp", options["extractor_args"])
        self.assertFalse(extract.call_args.kwargs["download"])
        self.assertEqual(extract.call_args.kwargs["attempts"], 2)
        self.assertIn("LAY DUOC THONG TIN AUDIO", output.getvalue())

    def test_missing_audio_or_failed_extraction_reports_failure(self):
        for result in ({"title": "Only metadata", "formats": []}, None,
                       RuntimeError("HTTP Error 429: Too Many Requests")):
            with (
                self.subTest(result=result),
                patch.object(probe, "youtube_ydl_options", return_value={}),
                patch.object(probe, "extract_info_with_retry") as extract,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                if isinstance(result, Exception):
                    extract.side_effect = result
                else:
                    extract.return_value = result
                self.assertEqual(probe.probe(), 1)
                self.assertIn("THU IPv4 THAT BAI", output.getvalue())


if __name__ == "__main__":
    unittest.main()
