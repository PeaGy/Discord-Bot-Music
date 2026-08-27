import unittest

from features.limbus_kit_view import build_ego_embeds, build_identity_kit_embeds
from features.limbus_wiki import (
    WikiPage,
    _asset_file_candidates,
    _asset_from_imageinfo_pages,
)


ASSET_URL = "https://cdn.example.test/identity-700px.png"


class LimbusAssetTests(unittest.TestCase):
    def test_file_candidates_cover_identity_profile_and_ego_icon(self):
        candidates = _asset_file_candidates(
            "The House of Spiders: The Index Nursefather Yi Sang"
        )
        self.assertIn(
            "The_House_of_Spiders_The_Index_Nursefather_Yi_Sang_Profile.png",
            candidates,
        )
        self.assertIn(
            "The_House_of_Spiders:_The_Index_Nursefather_Yi_Sang_Icon.png",
            candidates,
        )

    def test_file_candidates_replace_double_colon_with_a_space(self):
        candidates = _asset_file_candidates(
            "N Corp. E.G.O::Contempt, Awe Ryōshū", kind="identity"
        )
        self.assertIn(
            "N_Corp._E.G.O_Contempt,_Awe_Ryōshū_Profile.png",
            candidates,
        )

    def test_imageinfo_prefers_profile_before_icon(self):
        page = WikiPage(42, "Test Identity Yi Sang", "https://example.test", 9, "", "")
        candidates = _asset_file_candidates(page.title)
        asset = _asset_from_imageinfo_pages(
            [
                {
                    "title": "File:Test Identity Yi Sang Icon.png",
                    "imageinfo": [{"url": "https://cdn.example.test/icon.png"}],
                },
                {
                    "title": "File:Test Identity Yi Sang Profile.png",
                    "imageinfo": [
                        {
                            "url": "https://cdn.example.test/profile.png",
                            "thumburl": ASSET_URL,
                        }
                    ],
                },
            ],
            content_page=page,
            candidates=candidates,
        )
        self.assertIsNotNone(asset)
        self.assertEqual(asset["asset_url"], ASSET_URL)

    def test_full_identity_uses_asset_on_overview_only(self):
        embeds = build_identity_kit_embeds(
            {
                "title": "Test Identity Yi Sang",
                "url": "https://example.test/wiki/Test",
                "asset_url": ASSET_URL,
                "display_mode": "full_kit",
                "skills": [
                    {
                        "label": "Skill 1",
                        "name": "Test Skill",
                        "sin": "Gloom",
                        "type": "Slash",
                        "base_power": 3,
                        "coin_power": "+4",
                        "coins": 1,
                    }
                ],
            }
        )
        self.assertEqual(embeds[0].thumbnail.url, ASSET_URL)
        self.assertIsNone(embeds[1].thumbnail.url)

    def test_single_skill_keeps_asset_on_the_skill_card(self):
        embeds = build_identity_kit_embeds(
            {
                "title": "Test Identity Yi Sang",
                "url": "https://example.test/wiki/Test",
                "asset_url": ASSET_URL,
                "display_mode": "single_skill",
                "skills": [
                    {
                        "label": "Skill 3",
                        "name": "Test Skill",
                        "sin": "Gloom",
                        "type": "Slash",
                        "base_power": 6,
                        "coin_power": "+3",
                        "coins": 2,
                    }
                ],
            }
        )
        self.assertEqual(len(embeds), 1)
        self.assertEqual(embeds[0].thumbnail.url, ASSET_URL)

    def test_ego_overview_uses_asset(self):
        embeds = build_ego_embeds(
            {
                "title": "Solemn Lament Yi Sang",
                "name": "Solemn Lament",
                "sinner": "Yi Sang",
                "url": "https://example.test/wiki/Solemn_Lament_Yi_Sang",
                "asset_url": ASSET_URL,
                "affinity": "Gloom",
                "skills": [],
            }
        )
        self.assertEqual(embeds[0].thumbnail.url, ASSET_URL)


if __name__ == "__main__":
    unittest.main()
