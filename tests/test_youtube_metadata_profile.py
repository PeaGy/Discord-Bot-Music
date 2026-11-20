import io
import unittest
from urllib.request import Request

import yt_dlp
from scripts.manual.profile_youtube_metadata import profile


class MetadataProfileTests(unittest.TestCase):
    def test_read_is_lazy_and_events_exclude_urls_and_credentials(self):
        class Session:
            def __init__(self, options):
                pass

            def urlopen(self, request):
                return io.BytesIO(b"metadata")

        def lookup(url):
            session = yt_dlp.YoutubeDL({"proxy": "http://private"})
            response = session.urlopen(Request(url))
            self.assertEqual(response.read(4), b"meta")
            self.assertEqual(response.read(), b"data")
            return {}

        status, events, start, end = profile("https://www.youtube.com/youtubei/v1/player?key=private", lookup, Session)
        self.assertEqual(status, "ok")
        self.assertEqual([name for _, _, name in events],
                         ["session-init configured-proxy", "player-api open", "player-api read", "player-api read"])
        self.assertTrue(all(start <= begin <= finish <= end for begin, finish, _ in events))

    def test_local_cache_bypasses_session_and_metadata_only_is_distinct(self):
        self.assertEqual(profile("unused", lambda url: {}, object)[:2], ("ok", []))
        self.assertEqual(profile("unused", lambda url: {"youtube_metadata_failed": True}, object)[0], "metadata-only")
