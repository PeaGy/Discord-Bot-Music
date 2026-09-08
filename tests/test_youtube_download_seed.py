"""Session handoff tests: synthetic metadata/cookies, no remote requests."""

import copy
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import Cookie
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yt_dlp
from yt_dlp.cookies import YoutubeDLCookieJar
import ytdlp_support as support


VIDEO_ID = "H5v3kku4y6Q"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def video(video_id=VIDEO_ID):
    return {
        "id": video_id, "title": "Offline fixture", "duration": 238,
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "extractor": "youtube", "extractor_key": "Youtube", "live_status": "not_live",
        "formats": [{"format_id": "251", "url": "https://cdn.test/?sig=SECRET",
                     "ext": "webm", "acodec": "opus", "vcodec": "none", "abr": 160}],
    }


class DownloadSeedTests(unittest.TestCase):
    def setUp(self):
        env = {name: "" for name in support._DOWNLOAD_SESSION_ENV}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        with support._download_seeds_lock:
            support._download_seeds.clear()
        self.addCleanup(support._download_seeds.clear)
        self.options = {"proxy": "http://user:SECRET@proxy.test:80", "quiet": True}
        self.jar = YoutubeDLCookieJar()
        self.jar.set_cookie(Cookie(
            version=0, name="VISITOR_INFO1_LIVE", value="SECRET", port=None,
            port_specified=False, domain=".youtube.com", domain_specified=True,
            domain_initial_dot=True, path="/", path_specified=True, secure=True,
            expires=None, discard=True, comment=None, comment_url=None, rest={},
        ))

    def remember(self, result=None, *, query=URL, options=None, at=10, enabled=True, validator=None):
        result = video() if result is None else result
        cookiejar = self.jar

        class FakeYDL:
            def __init__(self, opts):
                self.params = {**opts, "http_headers": {"User-Agent": "fixture-agent"}}
                self.cookiejar = cookiejar

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _query, *, download):
                return result

        with patch.object(yt_dlp, "YoutubeDL", FakeYDL), patch.object(support.time, "monotonic", return_value=at):
            returned = support.extract_info_with_retry(
                query, options if options is not None else self.options, download=False,
                remember_for_download=enabled, attempts=1, result_validator=validator,
            )
        self.assertIs(returned, result)
        return result

    def take(self, url=URL, *, at=11, options=None):
        with patch.object(support.time, "monotonic", return_value=at):
            return support.take_youtube_download_seed(url, self.options if options is None else options)

    def test_one_shot_alias_preserves_cookie_headers_and_caller_output(self):
        original = self.remember()
        seed = self.take(f"https://youtu.be/{VIDEO_ID}?si=share", options={**self.options, "outtmpl": "raw.%(ext)s", "format": "bestaudio/best"})
        self.assertIsNotNone(seed)
        self.assertEqual(seed.options["outtmpl"], "raw.%(ext)s")
        self.assertEqual(seed.options["format"], "bestaudio/best")
        self.assertEqual(seed.options["proxy"], self.options["proxy"])
        self.assertEqual(seed.options["http_headers"]["User-Agent"], "fixture-agent")
        self.assertEqual(list(seed.cookiejar)[0].value, "SECRET")
        self.assertIsNot(seed.cookiejar, self.jar)
        self.assertIsNone(seed.cookiejar.filename)
        self.assertNotIn("SECRET", repr(seed))
        self.assertIsNone(self.take())
        seed.info["title"] = "modified"
        self.assertEqual(original["title"], "Offline fixture")

    def test_old_info_is_never_reused(self):
        self.remember()
        self.assertIsNone(self.take(at=130))
        self.assertFalse(support._download_seeds)

    def test_configuration_changes_invalidate_seed(self):
        self.remember()
        self.assertIsNone(self.take(options={**self.options, "proxy": "http://different.test"}))
        self.remember()
        with patch.dict(os.environ, {"YTDLP_YOUTUBE_CLIENT": "mweb"}):
            self.assertIsNone(self.take())

    def test_cookiefile_snapshot_is_memory_only_and_replaced_file_invalidates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture-cookies.txt"
            path.write_text("synthetic fixture", encoding="utf-8")
            opts = {**self.options, "cookiefile": str(path)}
            self.remember(options=opts)
            seed = self.take(options=opts)
            self.assertNotIn("cookiefile", seed.options)
            self.assertNotIn("cookiesfrombrowser", seed.options)
            self.remember(options=opts)
            path.write_text("different synthetic fixture", encoding="utf-8")
            self.assertIsNone(self.take(options=opts))

    def test_only_opted_in_short_resolved_youtube_audio_is_eligible(self):
        invalid = [
            {**video(), "duration": 0}, {**video(), "duration": 601},
            {**video(), "duration": float("nan")}, {**video(), "is_live": True},
            {**video(), "live_status": "post_live"}, {**video(), "formats": []},
            {**video(), "_type": "url"}, {**video(), "webpage_url": "https://soundcloud.com/a/b"},
            {**video(), "id": "different"}, {**video(), "additional_urls": [URL]},
            {"_type": "playlist", "entries": [video()]},
        ]
        for info in invalid:
            with self.subTest(info=info.get("_type") or info.get("duration")):
                self.remember(info)
                self.assertIsNone(self.take())
        self.remember(enabled=False)
        self.assertIsNone(self.take())
        self.remember(validator=lambda _info: False)
        self.assertIsNone(self.take())

    def test_search_single_result_works_but_multiple_entries_are_rejected(self):
        self.remember({"_type": "playlist", "entries": [video()]}, query="ytsearch1:fixture")
        self.assertIsNotNone(self.take())
        self.remember({"_type": "playlist", "entries": [video(), video()]}, query="ytsearch1:fixture")
        self.assertIsNone(self.take())

    def test_stale_filename_bookkeeping_is_removed(self):
        self.remember({**video(), "requested_downloads": [{"filepath": "old"}],
                       "__files_to_move": {"old": "old"}, "filepath": "old", "_filename": "old"})
        seed = self.take()
        for field in ("requested_downloads", "__files_to_move", "filepath", "_filename"):
            self.assertNotIn(field, seed.info)
        self.assertTrue(seed.info["formats"])

    def test_direct_success_keeps_empty_proxy_and_no_account_cookies(self):
        opts = {**self.options, "cookiefile": ".not-a-real-cookie-fixture"}
        account_jar = self.jar

        class FakeYDL:
            def __init__(self, options):
                self.params = options
                self.cookiejar = account_jar if options.get("proxy") else YoutubeDLCookieJar()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _query, *, download):
                if self.params.get("proxy"):
                    raise RuntimeError("HTTP Error 403: Forbidden")
                return video()

        with patch.dict(os.environ, {"YTDLP_YOUTUBE_DIRECT_FALLBACK": "true", "YTDLP_YOUTUBE_CLIENT": "mweb"}), patch.object(
            yt_dlp, "YoutubeDL", FakeYDL
        ):
            support.extract_info_with_retry(URL, opts, attempts=1, retry_delay=0, remember_for_download=True)
            seed = support.take_youtube_download_seed(URL, opts)
        self.assertIsNotNone(seed)
        self.assertEqual(seed.route_name, "direct")
        self.assertEqual(seed.player_client, "mweb")
        self.assertEqual(seed.options["proxy"], "")
        self.assertNotIn("cookiefile", seed.options)
        self.assertFalse(list(seed.cookiejar))

    def test_entry_count_is_bounded(self):
        for index in range(33):
            self.remember(video(f"{index:011d}"), query=f"https://youtu.be/{index:011d}")
        self.assertEqual(len(support._download_seeds), 32)
        self.assertIsNone(self.take("https://youtu.be/00000000000"))
        self.assertIsNotNone(self.take("https://youtu.be/00000000032"))

    def test_two_workers_cannot_consume_same_seed(self):
        self.remember(at=support.time.monotonic())
        with ThreadPoolExecutor(max_workers=2) as pool:
            seeds = list(pool.map(lambda _: support.take_youtube_download_seed(URL, self.options), range(2)))
        self.assertEqual(sum(seed is not None for seed in seeds), 1)

    def test_real_ytdlp_cookiejar_and_default_headers_support_handoff(self):
        original_class = yt_dlp.YoutubeDL
        opts = {**self.options, "cachedir": False}

        def extract(ydl, _query, *, download):
            for cookie in self.jar:
                ydl.cookiejar.set_cookie(copy.copy(cookie))
            return ydl.process_ie_result(video(), download=False)

        with patch.object(original_class, "extract_info", extract):
            info = support.extract_info_with_retry(URL, opts, remember_for_download=True, attempts=1)
        seed = support.take_youtube_download_seed(URL, {**opts, "outtmpl": "raw.%(ext)s"})
        self.assertIsNotNone(seed)
        self.assertEqual(seed.info["id"], info["id"])
        self.assertEqual(list(seed.cookiejar)[0].value, "SECRET")
        self.assertTrue(seed.options["http_headers"])
        self.assertNotIn("SECRET", repr(seed))


if __name__ == "__main__":
    unittest.main()
