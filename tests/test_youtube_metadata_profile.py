import io
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from urllib.request import Request

import yt_dlp
from scripts.manual import compare_youtube_runtimes as ab
from scripts.manual.profile_youtube_metadata import PlayerTail, profile


class MetadataProfileTests(unittest.TestCase):
    def test_compare_alternates_and_rejects_unverified_trials(self):
        good = '{"total": 4, "js": 2, "route": "proxy"}'
        for payloads, expected in (([good] * 6, 0), (['{"skip":"cache hit"}'], 1),
                                   ([good, good.replace("proxy", "direct")], 1)):
            with patch.object(ab.shutil, "which", return_value="exe"), patch.object(
                ab.subprocess, "run", side_effect=[SimpleNamespace(returncode=0, stdout=s) for s in payloads]
            ) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(ab.compare("video"), expected)
                self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                                 (["node", "deno"] * 3)[:len(payloads)])

    def test_detail_only_keeps_calls_after_last_player_read(self):
        class Session:
            def __init__(self, options):
                pass

            def urlopen(self, request):
                return io.BytesIO(b"data")

        def lookup(url):
            response = yt_dlp.YoutubeDL({}).urlopen(url)
            response.read(2)
            exec(compile("pass", "/private/yt_dlp/earlier.py", "exec"))
            response.read()
            exec(compile("pass", "/private/yt_dlp/later.py", "exec"))
            return {}

        tail = PlayerTail()
        status, _, _, end = profile("https://youtube.com/youtubei/v1/player", lookup, Session, tail)
        self.assertEqual(status, "ok")
        out = io.StringIO()
        with redirect_stdout(out):
            tail.report(end)
        self.assertIn("later.py", out.getvalue())
        self.assertNotIn("earlier.py", out.getvalue())
        self.assertNotIn("private", out.getvalue())

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
