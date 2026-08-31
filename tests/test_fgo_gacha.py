import io
import random
import unittest

from PIL import Image

from features._fgo_gacha import (
    CANVAS_SIZE,
    FGOCard,
    FGOPull,
    mark_new_fgo_pulls,
    parse_fgo_pool,
    pull_fgo,
    render_fgo_result,
)


def servant(card_id, name, rarity, *, flag="normal", card_type="normal"):
    return {
        "id": card_id,
        "collectionNo": card_id,
        "name": name,
        "rarity": rarity,
        "type": card_type,
        "flag": flag,
        "face": f"https://example.test/servant-{card_id}.png",
    }


def equip(card_id, name, rarity, *, flag="normal"):
    return {
        "id": card_id,
        "collectionNo": card_id,
        "name": name,
        "rarity": rarity,
        "type": "servantEquip",
        "flag": flag,
        "face": f"https://example.test/equip-{card_id}.png",
    }


def complete_pool():
    servants = [servant(100 + rarity, f"Servant {rarity}", rarity) for rarity in (3, 4, 5)]
    equips = [equip(200 + rarity, f"CE {rarity}", rarity) for rarity in (3, 4, 5)]
    return parse_fgo_pool(servants, equips)


class FixedRandom(random.Random):
    def __init__(self, value):
        super().__init__(0)
        self.value = value

    def random(self):
        return self.value

    def choice(self, sequence):
        return sequence[0]

    def shuffle(self, sequence):
        return None


class FGODataAndRollTests(unittest.TestCase):
    def test_parser_excludes_internal_servants_and_nonstandard_ces(self):
        servants = [
            *(servant(100 + rarity, f"Servant {rarity}", rarity) for rarity in (3, 4, 5)),
            servant(999, "Internal", 4, flag="ignoreCombineLimitSpecial"),
            servant(998, "Enemy", 5, card_type="enemyCollectionDetail"),
        ]
        equips = [
            *(equip(200 + rarity, f"CE {rarity}", rarity) for rarity in (3, 4, 5)),
            equip(997, "Bond CE", 4, flag="svtEquipFriendShip"),
            equip(996, "Event CE", 5, flag="svtEquipEvent"),
        ]

        pool = parse_fgo_pool(servants, equips, "global")

        self.assertEqual(pool.region, "na")
        servant_names = {card.name for cards in pool.servants.values() for card in cards}
        ce_names = {card.name for cards in pool.craft_essences.values() for card in cards}
        self.assertNotIn("Internal", servant_names)
        self.assertNotIn("Enemy", servant_names)
        self.assertNotIn("Bond CE", ce_names)
        self.assertNotIn("Event CE", ce_names)

    def test_single_rate_bands_match_reference_simulator(self):
        pool = complete_pool()
        expected = (
            (0.001, "servant", 5),
            (0.02, "ce", 5),
            (0.06, "servant", 4),
            (0.10, "ce", 4),
            (0.30, "servant", 3),
            (0.90, "ce", 3),
        )
        for value, category, rarity in expected:
            with self.subTest(value=value):
                card = pull_fgo(pool, 1, FixedRandom(value))[0].card
                self.assertEqual((card.category, card.rarity), (category, rarity))

    def test_eleven_roll_guarantees_servant_and_four_star_or_better(self):
        pulls = pull_fgo(complete_pool(), 11, FixedRandom(0.99))

        self.assertEqual(len(pulls), 11)
        self.assertTrue(any(pull.card.category == "servant" for pull in pulls))
        self.assertTrue(any(pull.card.rarity >= 4 for pull in pulls))

    def test_new_marker_only_marks_first_unowned_copy(self):
        card = complete_pool().cards("servant", 5)[0]
        marked = mark_new_fgo_pulls(
            (FGOPull(card), FGOPull(card)),
            {card.kind: set()},
        )
        self.assertTrue(marked[0].is_new)
        self.assertFalse(marked[1].is_new)


class FGOPresentationTests(unittest.TestCase):
    def test_renderer_builds_eleven_card_png(self):
        pool = complete_pool()
        cards = [
            pool.cards("servant" if index % 2 else "ce", 3 + index % 3)[0]
            for index in range(11)
        ]
        pulls = tuple(FGOPull(card, is_new=index == 0) for index, card in enumerate(cards))
        art = Image.new("RGB", (256, 256), (76, 126, 180))
        raw = io.BytesIO()
        art.save(raw, format="PNG")
        image_data = {card.image_url: raw.getvalue() for card in cards}

        rendered = render_fgo_result(pulls, image_data, region_label="NA/Global")

        with Image.open(io.BytesIO(rendered)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, CANVAS_SIZE)


if __name__ == "__main__":
    unittest.main()
