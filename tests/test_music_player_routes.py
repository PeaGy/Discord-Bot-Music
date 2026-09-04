import unittest

from music.player import _needs_stream_lookup, _youtube_entry_url


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


if __name__ == "__main__":
    unittest.main()
