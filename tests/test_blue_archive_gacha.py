import io
import random
import unittest
from pathlib import Path
from PIL import Image

from features._blue_archive_gacha import (
    CANVAS_SIZE,
    KIND_STAR1,
    KIND_STAR2,
    KIND_STAR3,
    BlueArchivePull,
    _student_icon,
    mark_new_blue_archive_pulls,
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

    def test_new_label_is_only_applied_to_first_unowned_copy(self):
        banner = parse_blue_archive_banner(self.config, self.students, "global")
        pickup = banner.pickups[0]
        permanent = banner.three_star[0]
        marked = mark_new_blue_archive_pulls(
            (
                BlueArchivePull(pickup, is_pickup=True),
                BlueArchivePull(pickup, is_pickup=True),
                BlueArchivePull(permanent),
            ),
            {KIND_STAR3: {permanent.name}},
        )
        self.assertTrue(marked[0].is_new)
        self.assertFalse(marked[1].is_new)
        self.assertFalse(marked[2].is_new)


class BlueArchivePresentationTests(unittest.TestCase):
    def test_student_icon_preserves_transparent_corners(self):
        artwork = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        artwork.paste((90, 180, 230, 255), (24, 16, 104, 128))
        raw = io.BytesIO()
        artwork.save(raw, format="PNG")

        fitted = _student_icon(raw.getvalue(), (160, 160))

        self.assertEqual(fitted.mode, "RGBA")
        self.assertEqual(fitted.getpixel((0, 0))[3], 0)
        self.assertGreater(fitted.getpixel((80, 80))[3], 0)

    def test_reference_ui_assets_are_packaged(self):
        asset_dir = Path(__file__).resolve().parent.parent / "assets" / "blue_archive_gacha"
        for filename in ("Background.png", "New.png", "Point.png", "Star.png"):
            with self.subTest(filename=filename):
                with Image.open(asset_dir / filename) as image:
                    self.assertGreater(image.width, 0)
                    self.assertGreater(image.height, 0)

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
