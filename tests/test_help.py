import unittest

import discord

from commands.help import (
    HELP_PAGE_DESCRIPTION_LIMIT,
    HelpDropdown,
    split_help_description,
)


class HelpPaginationTests(unittest.TestCase):
    def test_game_category_is_available(self):
        view = HelpDropdown()
        values = {
            option.value
            for child in view.children
            if isinstance(child, discord.ui.Select)
            for option in child.options
        }
        self.assertIn("games", values)

    def test_short_description_stays_on_one_page(self):
        self.assertEqual(split_help_description("hello"), ["hello"])

    def test_long_description_is_split_below_discord_limit(self):
        description = "\n\n".join(
            f"**Section {index}**\n" + ("content " * 180)
            for index in range(8)
        )
        pages = split_help_description(description)

        self.assertGreater(len(pages), 1)
        self.assertTrue(all(0 < len(page) <= HELP_PAGE_DESCRIPTION_LIMIT for page in pages))
        self.assertEqual("\n\n".join(pages), description)

    def test_single_oversized_paragraph_is_split_on_lines(self):
        description = "\n".join("x" * 100 for _ in range(100))
        pages = split_help_description(description)

        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= HELP_PAGE_DESCRIPTION_LIMIT for page in pages))


if __name__ == "__main__":
    unittest.main()
