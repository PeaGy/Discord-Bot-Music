import unittest
from unittest.mock import patch

from features import media_downloader as media


class XMediaDownloaderTests(unittest.TestCase):
    def test_probe_x_photo_post_uses_original_images(self) -> None:
        payload = {
            "url": "https://x.com/artist/status/1234567890",
            "text": "Hai ảnh minh họa",
            "author": {"name": "Artist"},
            "media": {
                "photos": [
                    {
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/one.jpg?name=small",
                    },
                    {
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/two.png",
                    },
                ]
            },
        }

        with patch.object(media, "_fxtwitter_status_sync", return_value=payload):
            item = media._probe_x_special_media_sync(
                "https://x.com/artist/status/1234567890"
            )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.media_kind, "photo")
        self.assertEqual(len(item.image_sources), 2)
        self.assertTrue(all("name=orig" in sources[0] for sources in item.image_sources))
        self.assertEqual(
            media._build_media_embed(item).fields[1].value,
            "Bài đăng ảnh X / Twitter",
        )


    def test_probe_x_gif_offers_gif_and_mp4_source(self) -> None:
        payload = {
            "url": "https://x.com/artist/status/9876543210",
            "text": "Animation",
            "author": {"name": "Animator"},
            "media": {
                "videos": [{
                    "type": "gif",
                    "duration": 3.5,
                    "thumbnail_url": "https://pbs.twimg.com/tweet_video_thumb/demo.jpg",
                    "formats": [
                        {
                            "container": "mp4",
                            "height": 360,
                            "bitrate": 256000,
                            "url": "https://video.twimg.com/tweet_video/demo.mp4",
                        },
                        {
                            "container": "mp4",
                            "height": 720,
                            "bitrate": 832000,
                            "url": "https://video.twimg.com/tweet_video/demo-high.mp4",
                        },
                    ],
                }]
            },
        }

        with patch.object(media, "_fxtwitter_status_sync", return_value=payload):
            item = media._probe_x_special_media_sync(
                "https://twitter.com/artist/status/9876543210"
            )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.media_kind, "gif")
        self.assertEqual(item.output_format, "gif")
        self.assertEqual(item.duration, 4)
        self.assertEqual(
            item.direct_url,
            "https://video.twimg.com/tweet_video/demo-high.mp4",
        )
        self.assertEqual(media.MediaDownloadButton(item).label, "Tải GIF")


    def test_probe_x_regular_video_stays_on_existing_downloader(self) -> None:
        payload = {
            "media": {
                "videos": [{
                    "type": "video",
                    "url": "https://video.twimg.com/ext_tw_video/demo.mp4",
                }]
            }
        }

        with patch.object(media, "_fxtwitter_status_sync", return_value=payload):
            item = media._probe_x_special_media_sync(
                "https://x.com/artist/status/1111111111"
            )

        self.assertIsNone(item)


if __name__ == "__main__":
    unittest.main()
