import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from features.projectmoon_youtube import (
    ProjectMoonYouTube,
    YouTubeFeedEntry,
    build_video_embed,
    matches_limbus_keywords,
    parse_youtube_feed,
)
from guild_settings import GuildNotification


SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom"
      xmlns:media="http://search.yahoo.com/mrss/">
  <title>ProjectMoon Official</title>
  <entry>
    <yt:videoId>abc123</yt:videoId>
    <yt:channelId>UCpqyr6h4RCXCEswHlkSjykA</yt:channelId>
    <title>LimbusCompany [000] Test Identity</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
    <published>2026-08-27T08:00:00+00:00</published>
    <updated>2026-08-27T08:01:00+00:00</updated>
    <media:group>
      <media:title>LimbusCompany [000] Test Identity</media:title>
      <media:thumbnail url="https://i.ytimg.com/vi/abc123/hqdefault.jpg"/>
      <media:description>New Limbus Company extraction.</media:description>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>other456</yt:videoId>
    <title>Library of Ruina soundtrack</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=other456"/>
    <published>2026-08-26T08:00:00Z</published>
    <updated>2026-08-26T08:01:00Z</updated>
    <media:group>
      <media:description>Not a Limbus upload.</media:description>
    </media:group>
  </entry>
</feed>
"""


class ProjectMoonYouTubeTests(unittest.TestCase):
    def test_parse_feed_extracts_video_metadata(self):
        entries = parse_youtube_feed(SAMPLE_FEED)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].video_id, "abc123")
        self.assertEqual(entries[0].channel_name, "ProjectMoon Official")
        self.assertEqual(
            entries[0].thumbnail_url,
            "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
        )
        self.assertEqual(entries[0].published.year, 2026)

    def test_keyword_filter_is_case_insensitive(self):
        entries = parse_youtube_feed(SAMPLE_FEED)

        self.assertTrue(matches_limbus_keywords(entries[0], ("limbuscompany",)))
        self.assertFalse(matches_limbus_keywords(entries[1], ("limbuscompany",)))
        self.assertTrue(matches_limbus_keywords(entries[1], ()))

    def test_preview_embed_uses_official_link_and_thumbnail(self):
        entry = parse_youtube_feed(SAMPLE_FEED)[0]
        embed = build_video_embed(entry, preview=True)

        self.assertEqual(embed.title, entry.title)
        self.assertEqual(embed.url, entry.url)
        self.assertEqual(embed.image.url, entry.thumbnail_url)
        self.assertIn("Bản xem thử", embed.footer.text)

    def test_invalid_xml_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "XML không hợp lệ"):
            parse_youtube_feed(b"<feed>")


class ProjectMoonYouTubeStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_poll_seeds_then_each_new_video_announces_once(self):
        old_entry = parse_youtube_feed(SAMPLE_FEED)[0]
        new_entry = YouTubeFeedEntry(
            video_id="new789",
            title="LimbusCompany new trailer",
            url="https://www.youtube.com/watch?v=new789",
            published=old_entry.published,
            updated=old_entry.updated,
            description="Limbus Company update",
            thumbnail_url="https://i.ytimg.com/vi/new789/hqdefault.jpg",
            channel_name="ProjectMoon Official",
        )

        with TemporaryDirectory() as directory:
            cog = ProjectMoonYouTube(bot=object())
            cog.db_path = Path(directory) / "youtube.db"
            cog._announce = AsyncMock(return_value=(1, 0))
            await cog._init_db()

            cog._fetch_feed = AsyncMock(return_value=[old_entry])
            self.assertEqual(await cog.poll_once(), ("seeded", 0))
            cog._announce.assert_not_awaited()

            cog._fetch_feed = AsyncMock(return_value=[new_entry, old_entry])
            self.assertEqual(await cog.poll_once(), ("checked", 1))
            cog._announce.assert_awaited_once()

            self.assertEqual(await cog.poll_once(), ("checked", 0))
            self.assertEqual(cog._announce.await_count, 1)

    async def test_failed_guild_retries_without_reposting_to_successful_guild(self):
        entry = parse_youtube_feed(SAMPLE_FEED)[0]
        first = GuildNotification(
            1, "projectmoon", "official_youtube", True, 10, None, 0, 1, 1
        )
        second = GuildNotification(
            2, "projectmoon", "official_youtube", True, 20, None, 0, 1, 1
        )
        successful_channel = AsyncMock()

        with TemporaryDirectory() as directory:
            cog = ProjectMoonYouTube(bot=object())
            cog.db_path = Path(directory) / "youtube.db"
            await cog._init_db()
            cog._destinations = AsyncMock(return_value=[first, second])

            async def resolve(channel_id):
                if channel_id == 20:
                    raise RuntimeError("missing permissions")
                return successful_channel

            cog._resolve_destination = AsyncMock(side_effect=resolve)

            self.assertEqual(
                await cog._announce(entry, first_seen_at=1),
                (1, 1),
            )
            self.assertEqual(
                await cog._announce(entry, first_seen_at=1),
                (0, 1),
            )
            successful_channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
