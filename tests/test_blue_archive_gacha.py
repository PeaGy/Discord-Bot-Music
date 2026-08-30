import io
import random
import unittest
from pathlib import Path

from PIL import Image

from features._blue_archive_gacha import (
    ASSET_DIR,
    CANVAS_SIZE,
    KIND_STAR1,
    KIND_STAR2,
    KIND_STAR3,
    gif_cycle_duration_seconds,
    parse_blue_archive_banner,
    pull_blue_archive,
    render_blue_archive_result,
)


def student(student_id, name, star, limited, *, released=True):
    return {
        "Id": student_id,
        "Name": name,
        "StarGrade": star,
        "IsReleased": [released, released, released],
        "IsLimited": [limited, limited, limited],
        "School": "Test",
    }


class SequenceRandom(random.Random):
    def __init__(self, values):
        super().__init__(0)
        self.values = iter(values)

    def random(self):
        return next(self.values)

    def choice(self, sequence):
        return sequence[0]


class BlueArchiveDataTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "Regions": [
                {
                    "Name": "Global",
                    "CurrentGacha": [
                        {"characters": [101, 102], "start": 1000, "end": 2000}
                    ],
                }
            ]
        }
        self.students = {
            "1": student(1, "One", 1, 0),
            "2": student(2, "Two", 2, 0),
            "3": student(3, "Permanent Three", 3, 4),
            "101": student(101, "Pickup A", 3, 1),
            "102": student(102, "Pickup B", 3, 1),
            "999": student(999, "Welfare", 3, 2),
        }

    def test_current_banner_excludes_welfare_and_keeps_active_pickups(self):
        banner = parse_blue_archive_banner(self.config, self.students, "global")
        self.assertEqual([item.name for item in banner.pickups], ["Pickup A", "Pickup B"])
        self.assertEqual(banner.banner_id, "global:1000:2000")
        names = {item.name for item in banner.three_star}
        self.assertEqual(names, {"Permanent Three", "Pickup A", "Pickup B"})
        self.assertNotIn("Welfare", names)

    def test_tenth_slot_is_always_two_star_or_better(self):
        banner = parse_blue_archive_banner(self.config, self.students, "global")
        target = banner.pickups[0]
        pulls = pull_blue_archive(
            banner,
            target,
            10,
            SequenceRandom([0.9] * 10),
        )
        self.assertTrue(all(item.student.kind == KIND_STAR1 for item in pulls[:9]))
        self.assertEqual(pulls[-1].student.kind, KIND_STAR2)

    def test_rate_bands_select_pickup_other_three_two_and_one(self):
        banner = parse_blue_archive_banner(self.config, self.students, "global")
        target = banner.pickups[0]
        expected = (KIND_STAR3, KIND_STAR3, KIND_STAR2, KIND_STAR1)
        for value, kind in zip((0.001, 0.01, 0.1, 0.9), expected):
            result = pull_blue_archive(
                banner, target, 1, SequenceRandom([value])
            )[0]
            self.assertEqual(result.student.kind, kind)


class BlueArchivePresentationTests(unittest.TestCase):
    def test_user_gifs_are_packaged_and_keep_exact_first_cycle_duration(self):
        normal = ASSET_DIR / "normal.gif"
        special = ASSET_DIR / "special.gif"
        self.assertTrue(normal.is_file())
        self.assertTrue(special.is_file())
        self.assertAlmostEqual(gif_cycle_duration_seconds(normal), 9.59, places=2)
        self.assertAlmostEqual(gif_cycle_duration_seconds(special), 8.67, places=2)

    def test_result_renderer_builds_a_two_by_five_png(self):
        config = {
            "Regions": [
                {
                    "Name": "Global",
                    "CurrentGacha": [{"characters": [3], "start": 1, "end": 2}],
                }
            ]
        }
        students = {
            "1": student(1, "One", 1, 0),
            "2": student(2, "Two", 2, 0),
            "3": student(3, "Three", 3, 4),
        }
        banner = parse_blue_archive_banner(config, students)
        pulls = pull_blue_archive(banner, banner.pickups[0], 10, SequenceRandom([0.9] * 10))
        artwork = Image.new("RGB", (128, 128), (90, 180, 230))
        raw = io.BytesIO()
        artwork.save(raw, format="PNG")
        image_data = {pull.student.image_url: raw.getvalue() for pull in pulls}
        rendered = render_blue_archive_result(
            pulls,
            image_data,
            region_label="Global",
            target_name="Three",
            recruitment_points=10,
        )
        with Image.open(io.BytesIO(rendered)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, CANVAS_SIZE)


if __name__ == "__main__":
    unittest.main()
