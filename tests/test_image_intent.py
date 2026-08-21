import unittest

from features.ai_chat import GrokChat


class ImageEditIntentTests(unittest.TestCase):
    def test_polish_text_is_not_image_edit(self):
        text = (
            "Câu what are solutions to traffic jams. Peto đưa ra 2 giải pháp "
            "và kèm theo lời giải thích cho nó chỉnh chu hơn tí"
        )
        self.assertFalse(GrokChat._user_wants_edit_image(text))
        self.assertFalse(GrokChat._should_edit_with_source(text, True))

    def test_explicit_image_edits_are_detected(self):
        samples = (
            "Peto chỉnh ảnh này sáng hơn giúp tôi",
            "peto chinh anh nay sang hon",
            "thêm chữ Peto vào ảnh",
            "xóa nền trắng",
            "edit this",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertTrue(GrokChat._user_wants_edit_image(text))

    def test_general_text_changes_are_not_image_edits(self):
        samples = (
            "thêm hai giải pháp và giải thích kỹ hơn",
            "đổi câu trả lời sang tiếng Anh",
            "sửa ngữ pháp cho đoạn này",
            "viết cho chỉnh chu hơn",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertFalse(GrokChat._user_wants_edit_image(text))


if __name__ == "__main__":
    unittest.main()
