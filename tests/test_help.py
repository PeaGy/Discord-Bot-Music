import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from peto_help_catalog import HELP_CATEGORIES

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
        self.assertEqual(values, set(HELP_CATEGORIES))

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


class HelpCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_dropdown_category_uses_shared_title_and_paginated_description(self):
        for key, (title, description) in HELP_CATEGORIES.items():
            with self.subTest(category=key):
                view = HelpDropdown()
                interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
                await HelpDropdown.select_callback(view, interaction, SimpleNamespace(values=[key]))
                self.assertEqual(view.current_title, title)
                self.assertEqual(view.current_pages, split_help_description(description))
                self.assertEqual(view.current_page, 0)
                self.assertTrue(view.previous_page.disabled)
                self.assertEqual(view.next_page.disabled, len(view.current_pages) <= 1)
                message = interaction.response.edit_message.await_args.kwargs
                self.assertEqual(message["embed"].title, title)
                self.assertEqual(message["embed"].description, view.current_pages[0])
                self.assertIs(message["view"], view)

    async def test_catalog_changes_are_used_without_duplicated_panel_copy(self):
        view = HelpDropdown()
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        with patch.dict(HELP_CATEGORIES, {"info": ("Updated public title", "Updated public description")}):
            await HelpDropdown.select_callback(view, interaction, SimpleNamespace(values=["info"]))
        self.assertEqual(view.current_title, "Updated public title")
        self.assertEqual(view.current_pages, ["Updated public description"])

    async def test_unknown_selection_preserves_media_fallback(self):
        view = HelpDropdown()
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await HelpDropdown.select_callback(view, interaction, SimpleNamespace(values=["unknown"]))
        title, description = HELP_CATEGORIES["media"]
        self.assertEqual(view.current_title, title)
        self.assertEqual(view.current_pages, split_help_description(description))


if __name__ == "__main__":
    unittest.main()
