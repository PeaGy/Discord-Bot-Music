import unittest
from pathlib import Path
from types import SimpleNamespace

from features.welcome import (
    DEFAULT_WELCOME_GIF_URL,
    DEFAULT_WELCOME_TITLE,
    WELCOME_RED,
    build_welcome_embed,
)


class WelcomeEmbedTests(unittest.TestCase):
    def test_bundled_gif_is_valid(self):
        header = Path("assets/welcome_teto.gif").read_bytes()[:6]
        self.assertIn(header, {b"GIF87a", b"GIF89a"})

    def test_embed_uses_member_avatar_and_requested_gif(self):
        member = SimpleNamespace(
            mention="<@123>",
            display_name="New Member",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
            guild=SimpleNamespace(name="Peto's Server", member_count=42),
        )

        embed = build_welcome_embed(member)

        self.assertEqual(embed.title, DEFAULT_WELCOME_TITLE)
        self.assertEqual(embed.color.value, WELCOME_RED)
        self.assertEqual(embed.thumbnail.url, "https://example.com/avatar.png")
        self.assertEqual(embed.image.url, DEFAULT_WELCOME_GIF_URL)
        self.assertIn("<@123>", embed.description)
        self.assertNotIn("42", embed.description)
        self.assertIsNone(embed.footer.text)

    def test_rules_and_roles_are_clickable_channel_mentions(self):
        member = SimpleNamespace(
            mention="<@123>",
            display_name="New Member",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
            guild=SimpleNamespace(name="Peto's Server", member_count=42),
        )

        embed = build_welcome_embed(
            member,
            rules_channel_id=111,
            roles_channel_id=222,
        )

        self.assertIn("<#111>", embed.description)
        self.assertIn("<#222>", embed.description)


if __name__ == "__main__":
    unittest.main()
