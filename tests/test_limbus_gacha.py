import io
import random
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PIL import Image

from features.limbus_gacha import (
    KIND_EGO,
    KIND_ID1,
    KIND_ID2,
    KIND_ID3,
    EGO_FRAME_GOLD,
    EGO_FRAME_RED,
    EGO_VISIBLE_SIZE,
    FRAME_ASSET_NAMES,
    GACHA_CANVAS_SIZE,
    GACHA_COLUMN_CENTERS,
    GACHA_ROW_CENTERS,
    GACHA_UI_DIR,
    IDENTITY_VISIBLE_SIZE,
    GachaEntry,
    GachaPool,
    LimbusGacha,
    RARITY_COLOR,
    _fallback_asset_url,
    _image_is_decodable,
    _is_walpurgis_page,
    _prepared_gacha_frame,
    _prepare_art_cache_dir,
    _render_artwork,
    _visible_alpha_bbox,
    load_gacha_pool_sync,
    load_exchange_catalog_sync,
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

    def test_ego_can_repeat_like_an_identity(self):
        pool = GachaPool(
            {
                KIND_EGO: (entry("Ego A", KIND_EGO),),
                KIND_ID3: (entry("Three A", KIND_ID3),),
                KIND_ID2: (entry("Two A", KIND_ID2),),
                KIND_ID1: (entry("One A", KIND_ID1),),
            }
        )
        result = pull_entries(pool, 10, FixedRandom(0.0))
        self.assertTrue(all(item.kind == KIND_EGO for item in result))
        self.assertTrue(all(item.name == "Ego A" for item in result))

    def test_slash_command_is_exposed(self):
        cog = LimbusGacha(bot=SimpleNamespace())
        self.assertEqual(cog.gacha.name, "gacha")
        self.assertEqual(cog.exchange_group.name, "exchange")
        self.assertEqual(cog.exchange_identity.name, "identity")
        self.assertEqual(cog.exchange_ego.name, "ego")

    def test_exchange_catalog_includes_event_but_excludes_walpurgis(self):
        identity_template = """| Info
| Rarity
|
| Season
| {season}
| Release
| 2025.02.06
Skills
Skill 1Skill 2Skill 3Defense
"""
        ego_template = """| Info
| Risk Level
|
| Season
| {season}
| Obtained
| {obtained}
| Cost
| Sanity
| 20 20
"""
        rarity_text = """There are 1Identities that have a 1★ rarity.
| LCB Sinner Ishmael
There are 1Identities that have a 2★ rarity.
| Kurokumo Clan Wakashu Hong Lu
There are 3Identities that have a 3★ rarity.
| Standard Captain Ishmael
| Kurokumo Clan Captain Ishmael
| Walpurgis Captain Ishmael
"""
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "wiki.db"
            with closing(sqlite3.connect(db_path)) as db:
                db.executescript(
                    """
                    CREATE TABLE wiki_pages (
                        pageid INTEGER PRIMARY KEY,
                        title TEXT NOT NULL,
                        url TEXT NOT NULL,
                        revid INTEGER NOT NULL DEFAULT 1,
                        timestamp TEXT NOT NULL DEFAULT '',
                        text TEXT NOT NULL,
                        indexed_at INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE wiki_assets (
                        pageid INTEGER PRIMARY KEY,
                        title TEXT NOT NULL,
                        revid INTEGER NOT NULL DEFAULT 1,
                        file_title TEXT NOT NULL DEFAULT '',
                        original_url TEXT NOT NULL DEFAULT '',
                        thumbnail_url TEXT NOT NULL DEFAULT '',
                        synced_at INTEGER NOT NULL DEFAULT 0
                    );
                    """
                )
                pages = [
                    (1, "List of Identities/Rarity", rarity_text),
                    (
                        2,
                        "List of E.G.O/Data",
                        "| Event E.G.O Ishmael\n| Walpurgis E.G.O Ishmael\n"
                        "| Battle Pass E.G.O Ishmael\n| Base E.G.O Ishmael",
                    ),
                    (3, "Standard Captain Ishmael", identity_template.format(season="Standard Fare")),
                    (4, "Kurokumo Clan Captain Ishmael", identity_template.format(season="Season 5 - Oblivion [Event]")),
                    (5, "Walpurgis Captain Ishmael", identity_template.format(season="Walpurgisnacht - IX")),
                    (6, "Event E.G.O Ishmael", ego_template.format(season="Season 5 - Oblivion [Event]", obtained="Event Rewards")),
                    (7, "Walpurgis E.G.O Ishmael", ego_template.format(season="Walpurgisnacht - IX", obtained="Limited Extraction")),
                    (8, "Battle Pass E.G.O Ishmael", ego_template.format(season="Season 5 - Oblivion", obtained="Battle Pass - Level 60")),
                    (9, "Base E.G.O Ishmael", ego_template.format(season="N/A", obtained="Base E.G.O")),
                ]
                db.executemany(
                    "INSERT INTO wiki_pages(pageid, title, url, text) VALUES(?, ?, 'https://example.com', ?)",
                    pages,
                )
                db.commit()

            catalog = load_exchange_catalog_sync(db_path)
            identity_names = {item.name for item in catalog.entries(KIND_ID3)}
            ego_names = {item.name for item in catalog.entries(KIND_EGO)}
            self.assertEqual(
                identity_names,
                {"Standard Captain Ishmael", "Kurokumo Clan Captain Ishmael"},
            )
            self.assertEqual(
                ego_names,
                {"Event E.G.O Ishmael", "Battle Pass E.G.O Ishmael"},
            )
            self.assertNotIn("Walpurgis Captain Ishmael", identity_names)
            self.assertNotIn("Walpurgis E.G.O Ishmael", ego_names)
            self.assertNotIn("Base E.G.O Ishmael", ego_names)

    def test_walpurgis_filter_reads_only_season_field(self):
        self.assertTrue(_is_walpurgis_page("| Season\n| Walpurgisnacht - IX\n| Release\n| 2026"))
        self.assertFalse(
            _is_walpurgis_page(
                "| Season\n| Season 5 - Oblivion [Event]\n| Notes\n| Mentions Walpurgis"
            )
        )

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
    def test_game_ui_assets_are_packaged_with_the_bot(self):
        self.assertTrue((GACHA_UI_DIR / "background.png").is_file())
        for filename in FRAME_ASSET_NAMES.values():
            path = GACHA_UI_DIR / filename
            self.assertTrue(path.is_file(), filename)
            with Image.open(path) as asset:
                self.assertEqual(asset.mode, "RGBA")
                self.assertIn(0, asset.getchannel("A").getextrema())

    def test_visible_frame_sizes_are_normalized_like_the_game(self):
        for kind in (KIND_ID1, KIND_ID2, KIND_ID3):
            frame, artwork_mask = _prepared_gacha_frame(kind)
            left, top, right, bottom = _visible_alpha_bbox(frame)
            self.assertAlmostEqual(right - left, IDENTITY_VISIBLE_SIZE[0], delta=2)
            self.assertAlmostEqual(bottom - top, IDENTITY_VISIBLE_SIZE[1], delta=2)
            art_left, art_top, art_right, art_bottom = artwork_mask.getbbox()
            self.assertGreaterEqual(art_right - art_left, 180)
            self.assertGreaterEqual(art_bottom - art_top, 110)

        ego_frame, ego_mask = _prepared_gacha_frame(KIND_EGO)
        left, top, right, bottom = _visible_alpha_bbox(ego_frame)
        self.assertAlmostEqual(right - left, EGO_VISIBLE_SIZE[0], delta=2)
        self.assertAlmostEqual(bottom - top, EGO_VISIBLE_SIZE[1], delta=2)
        self.assertGreater(EGO_VISIBLE_SIZE[1], IDENTITY_VISIBLE_SIZE[1])
        ego_left, ego_top, ego_right, ego_bottom = ego_mask.getbbox()
        self.assertLess(ego_right - ego_left, EGO_VISIBLE_SIZE[0] - 20)
        self.assertLess(ego_bottom - ego_top, EGO_VISIBLE_SIZE[1] - 20)

    def test_larger_slots_still_leave_space_between_results(self):
        column_gap = GACHA_COLUMN_CENTERS[1] - GACHA_COLUMN_CENTERS[0]
        row_gap = GACHA_ROW_CENTERS[1] - GACHA_ROW_CENTERS[0]
        self.assertGreater(column_gap, IDENTITY_VISIBLE_SIZE[0])
        self.assertGreater(row_gap, EGO_VISIBLE_SIZE[1])

    def test_identity_artwork_keeps_more_of_square_profile_image(self):
        source = Image.new("RGB", (256, 256), (210, 30, 50))
        for x in range(64):
            for y in range(256):
                source.putpixel((x, y), (20, 80, 220))
                source.putpixel((255 - x, y), (20, 190, 80))
        raw = io.BytesIO()
        source.save(raw, format="PNG")

        mask = Image.new("L", (180, 110), 255)
        rendered = _render_artwork(
            raw.getvalue(),
            mask.size,
            mask,
            ego=False,
        )

        self.assertIsNotNone(rendered)
        assert rendered is not None
        left = rendered.getpixel((24, 55))
        right = rendered.getpixel((155, 55))
        self.assertGreater(left[2], left[0])
        self.assertGreater(right[1], right[0])

    def test_collage_is_a_limbus_style_two_by_five_png(self):
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
            self.assertEqual(rendered.size, GACHA_CANVAS_SIZE)
            rgb = rendered.convert("RGB")
            for y in GACHA_ROW_CENTERS:
                for x in GACHA_COLUMN_CENTERS:
                    red, green, blue = rgb.getpixel((x, y))
                    self.assertGreater(red, 150)
                    self.assertLess(green, 80)
                    self.assertGreater(blue, 50)


if __name__ == "__main__":
    unittest.main()
