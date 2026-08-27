import io
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from features.limbus_gacha import (
    KIND_EGO,
    KIND_ID1,
    KIND_ID2,
    KIND_ID3,
    EGO_FRAME_GOLD,
    EGO_FRAME_RED,
    GachaEntry,
    GachaPool,
    LimbusGacha,
    RARITY_COLOR,
    _fallback_asset_url,
    _image_is_decodable,
    _prepare_art_cache_dir,
    load_gacha_pool_sync,
    parse_extraction_list,
    pull_entries,
    render_gacha_collage,
    roll_kind,
)


EXTRACTION_TEXT = """IdentitiesE.G.O
| Identities have an extraction rate of 2.9%, or 3% if all available E.G.O have been acquired.
|
Three A
Three B
| Identities have an extraction rate of 12.8%, or 13% if all available E.G.O have been acquired.
|
Two A
Two B
| Identities have an extraction rate of 83%, or 84% if all available E.G.O have been acquired.
|
One A
One B
|
Ego A
Ego B
"""


def entry(name: str, kind: str) -> GachaEntry:
    return GachaEntry(name, kind, f"https://example.com/{name}")


class FixedRandom:
    def __init__(self, value: float):
        self.value = value

    def random(self) -> float:
        return self.value

    def choice(self, items):
        return items[0]


class GachaParsingTests(unittest.TestCase):
    def test_rarity_colors_follow_limbus_extraction(self):
        red = RARITY_COLOR[KIND_ID2]
        gold = RARITY_COLOR[KIND_ID3]

        self.assertGreater(red[0], red[1] * 2)
        self.assertGreater(gold[0], 200)
        self.assertGreater(gold[1], 150)
        self.assertEqual(EGO_FRAME_GOLD, gold)
        self.assertGreater(EGO_FRAME_RED[0], EGO_FRAME_RED[1] * 3)

    def test_extraction_list_is_split_into_four_pools(self):
        pools = parse_extraction_list(EXTRACTION_TEXT)
        self.assertEqual(pools[KIND_ID3], ["Three A", "Three B"])
        self.assertEqual(pools[KIND_ID2], ["Two A", "Two B"])
        self.assertEqual(pools[KIND_ID1], ["One A", "One B"])
        self.assertEqual(pools[KIND_EGO], ["Ego A", "Ego B"])

    def test_rate_boundaries_follow_standard_extraction(self):
        self.assertEqual(roll_kind(FixedRandom(0.000)), KIND_EGO)
        self.assertEqual(roll_kind(FixedRandom(0.013)), KIND_ID3)
        self.assertEqual(roll_kind(FixedRandom(0.042)), KIND_ID2)
        self.assertEqual(roll_kind(FixedRandom(0.170)), KIND_ID1)

    def test_missing_asset_uses_mediawiki_file_redirect(self):
        url = _fallback_asset_url(
            "N Corp. E.G.O::Contempt, Awe Ryōshū", KIND_ID3
        )
        self.assertIn("Special:Redirect/file/", url)
        self.assertIn("N_Corp._E.G.O_Contempt,_Awe_Ry%C5%8Dsh%C5%AB_Profile.png", url)

    def test_cache_path_blocked_by_a_file_does_not_raise(self):
        with TemporaryDirectory() as directory:
            blocker = Path(directory) / ".cache"
            blocker.write_text("occupied", encoding="utf-8")
            self.assertIsNone(_prepare_art_cache_dir(blocker / "limbus_gacha"))

    def test_image_validation_rejects_invalid_cache_bytes(self):
        self.assertFalse(_image_is_decodable(b"RIFF-not-a-decodable-webp"))
        image = Image.new("RGB", (8, 8), (12, 34, 56))
        output = io.BytesIO()
        image.save(output, format="PNG")
        self.assertTrue(_image_is_decodable(output.getvalue()))

    def test_tenth_pull_never_returns_one_star(self):
        pool = GachaPool(
            {
                KIND_EGO: (entry("Ego", KIND_EGO),),
                KIND_ID3: (entry("Three", KIND_ID3),),
                KIND_ID2: (entry("Two", KIND_ID2),),
                KIND_ID1: (entry("One", KIND_ID1),),
            }
        )
        for seed in range(300):
            results = pull_entries(pool, 10, random.Random(seed))
            self.assertNotEqual(results[-1].kind, KIND_ID1)

    def test_slash_command_is_exposed(self):
        cog = LimbusGacha(bot=object())
        self.assertEqual(cog.gacha.name, "gacha")

    @unittest.skipUnless(
        Path("limbus_knowledge.db").is_file(), "local synced wiki database is absent"
    )
    def test_current_synced_database_builds_a_complete_pool(self):
        pool = load_gacha_pool_sync(Path("limbus_knowledge.db"))
        self.assertGreaterEqual(len(pool.entries(KIND_ID3)), 20)
        self.assertGreaterEqual(len(pool.entries(KIND_ID2)), 20)
        self.assertEqual(len(pool.entries(KIND_ID1)), 12)
        self.assertGreaterEqual(len(pool.entries(KIND_EGO)), 10)
        self.assertTrue(
            all(
                item.image_url
                for kind in (KIND_ID3, KIND_ID2, KIND_ID1, KIND_EGO)
                for item in pool.entries(kind)
            )
        )


class GachaCollageTests(unittest.TestCase):
    def test_collage_is_a_two_by_five_png(self):
        source = Image.new("RGB", (80, 120), (200, 30, 80))
        raw = io.BytesIO()
        source.save(raw, format="PNG")
        pulls = tuple(
            GachaEntry(
                f"Item {index}",
                (KIND_ID1, KIND_ID2, KIND_ID3, KIND_EGO)[index % 4],
                "https://example.com",
                "https://example.com/art.png",
            )
            for index in range(10)
        )
        result = render_gacha_collage(
            pulls, {"https://example.com/art.png": raw.getvalue()}
        )
        with Image.open(io.BytesIO(result)) as rendered:
            self.assertEqual(rendered.format, "PNG")
            self.assertGreater(rendered.width, rendered.height)
            self.assertEqual(rendered.width, 1082)
            self.assertEqual(rendered.height, 546)


if __name__ == "__main__":
    unittest.main()
