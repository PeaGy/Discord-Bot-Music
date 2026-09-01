import io
import unittest

from PIL import Image

from features._brown_dust_2_gacha import (
    BrownDust2Pity,
    mark_new_brown_dust_2_pulls,
    parse_brown_dust_2_pool,
    pull_brown_dust_2,
    render_brown_dust_2_result,
)


def companion(name: str, star: int):
    return {"title": {"Page": name, "name": name, "star": str(star)}}


def costume(
    costume_id: str,
    character: str,
    name: str,
    *,
    limited: int = 0,
    drawable: int = 1,
):
    return {
        "title": {
            "Page": f"{character}/{name}",
            "id": costume_id,
            "name": name,
            "charName": character,
            "isLimited": str(limited),
            "isDrawable": str(drawable),
        }
    }


def complete_pool():
    companions = []
    costumes = []
    next_id = 100000
    for rarity in (3, 4, 5):
        for index in range(5):
            character = f"Character {rarity}-{index}"
            companions.append(companion(character, rarity))
            costumes.append(costume(str(next_id), character, f"Costume {index}"))
            next_id += 1
    return parse_brown_dust_2_pool(costumes, companions)


class FixedRandom:
    def __init__(self, values):
        self.values = iter(values)

    def random(self):
        return next(self.values)

    @staticmethod
    def choice(values):
        return values[0]


class BrownDust2ParsingTests(unittest.TestCase):
    def test_pool_keeps_only_drawable_non_limited_costumes(self):
        companions = [companion(f"C{rarity}-{index}", rarity) for rarity in (3, 4, 5) for index in range(5)]
        costumes = [
            costume(f"{rarity}{index:05d}", f"C{rarity}-{index}", "Default")
            for rarity in (3, 4, 5)
            for index in range(5)
        ]
        costumes.extend(
            (
                costume("900001", "C5-0", "Limited", limited=1),
                costume("900002", "C5-0", "Unavailable", drawable=0),
            )
        )
        pool = parse_brown_dust_2_pool(costumes, companions)
        names = {entry.name for rarity in (3, 4, 5) for entry in pool.costumes(rarity)}
        self.assertEqual([len(pool.costumes(rarity)) for rarity in (3, 4, 5)], [5, 5, 5])
        self.assertNotIn("C5-0 — Limited", names)
        self.assertNotIn("C5-0 — Unavailable", names)
        self.assertIn("Costume_300000.png", pool.costumes(3)[0].image_url)

    def test_rates_and_four_star_pity_are_applied_on_the_tenth_pull(self):
        pool = complete_pool()
        pulls, pity = pull_brown_dust_2(
            pool,
            10,
            rng=FixedRandom([0.99] * 10),
        )
        self.assertEqual([pull.costume.rarity for pull in pulls], [3] * 9 + [4])
        self.assertTrue(pulls[-1].guaranteed_four_star)
        self.assertEqual(pity, BrownDust2Pity(since_four_star=0, since_five_star=10))

    def test_hundredth_pull_is_guaranteed_five_star(self):
        pool = complete_pool()
        pulls, pity = pull_brown_dust_2(
            pool,
            1,
            pity=BrownDust2Pity(since_four_star=3, since_five_star=99),
            rng=FixedRandom([]),
        )
        self.assertEqual(pulls[0].costume.rarity, 5)
        self.assertTrue(pulls[0].guaranteed_five_star)
        self.assertEqual(pity, BrownDust2Pity())

    def test_duplicate_costumes_are_kept_and_only_first_copy_is_new(self):
        pool = complete_pool()
        costume_entry = pool.costumes(3)[0]
        pulls, _ = pull_brown_dust_2(
            pool,
            10,
            rng=FixedRandom([0.99] * 10),
        )
        self.assertEqual(pulls[0].costume, costume_entry)
        marked = mark_new_brown_dust_2_pulls(pulls, {})
        self.assertTrue(marked[0].is_new)
        self.assertTrue(all(not pull.is_new for pull in marked[1:9]))

    def test_renderer_outputs_a_discord_sized_png(self):
        pool = complete_pool()
        pulls, _ = pull_brown_dust_2(
            pool,
            10,
            rng=FixedRandom([0.99] * 10),
        )
        png = render_brown_dust_2_result(pulls, {})
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (1280, 720))
            self.assertEqual(image.format, "PNG")


if __name__ == "__main__":
    unittest.main()
