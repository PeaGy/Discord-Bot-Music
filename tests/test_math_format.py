import unittest
from types import SimpleNamespace

from math_format import contains_latex, latex_to_discord
from features.ai_chat import GrokChat


class ReportedRegressionTests(unittest.TestCase):
    """Đúng nội dung Peto từng gửi ra Discord dưới dạng LaTeX thô."""

    def test_instrument_error_exercise(self):
        raw = (
            r"**Đề:** Đo chiều dài một thanh kim loại bằng thước chia milimét "
            r"(sai số dụng cụ \(\Delta l = 0{,}5\,\mathrm{mm}\)). "
            r"Kết quả đọc được là \(l = 12{,}4\,\mathrm{cm}\)."
        )
        self.assertEqual(
            latex_to_discord(raw),
            "**Đề:** Đo chiều dài một thanh kim loại bằng thước chia milimét "
            "(sai số dụng cụ Δl = 0,5 mm). Kết quả đọc được là l = 12,4 cm.",
        )

    def test_relative_error_and_measurement_list(self):
        self.assertEqual(
            latex_to_discord(r"Tính sai số tương đối \(\delta l\) (theo %)."),
            "Tính sai số tương đối δl (theo %).",
        )
        self.assertEqual(
            latex_to_discord(r"\(12{,}3\); \(12{,}4\); \(12{,}4\,\mathrm{cm}\)"),
            "12,3; 12,4; 12,4 cm",
        )


class ConversionTests(unittest.TestCase):
    def test_fraction_and_root(self):
        self.assertEqual(latex_to_discord(r"\(\frac{1}{2}\)"), "1/2")
        self.assertEqual(latex_to_discord(r"\(\frac{a+b}{c}\)"), "(a+b)/c")
        self.assertEqual(latex_to_discord(r"\(\sqrt{2}\)"), "√2")
        self.assertEqual(latex_to_discord(r"\(\sqrt{x+1}\)"), "√(x+1)")
        self.assertEqual(latex_to_discord(r"\(\sqrt[3]{8}\)"), "∛8")

    def test_scripts(self):
        self.assertEqual(latex_to_discord(r"\(x^2 + y^{10}\)"), "x² + y¹⁰")
        self.assertEqual(latex_to_discord(r"\(x^{-3}\)"), "x⁻³")
        self.assertEqual(latex_to_discord(r"\(a_{12}\)"), "a₁₂")
        self.assertEqual(latex_to_discord(r"\(30^\circ\)"), "30°")
        self.assertEqual(latex_to_discord(r"\(\int_0^1 x\,dx\)"), "∫₀¹ x dx")

    def test_display_math_becomes_its_own_line(self):
        self.assertEqual(
            latex_to_discord(r"Ta có $$\sum_{i=1}^{n} i$$ nên xong."),
            "Ta có\nΣᵢ₌₁ⁿ i\nnên xong.",
        )

    def test_latex_line_break_inside_environment(self):
        raw = "\\begin{align} x &= 2 \\\\ y &= 3 \\end{align}"
        self.assertEqual(latex_to_discord(raw), "x = 2\ny = 3")

    def test_command_swallows_one_space_before_a_letter(self):
        # LaTeX: `\Delta l` là "Δl", nhưng `\alpha + \beta` giữ khoảng trắng.
        self.assertEqual(latex_to_discord(r"\(\Delta t\)"), "Δt")
        self.assertEqual(latex_to_discord(r"\(\alpha + \beta\)"), "α + β")

    def test_unknown_command_is_left_alone(self):
        self.assertEqual(
            latex_to_discord(r"Xem \(\alpha\) trong \unknowncmd{x}"),
            r"Xem α trong \unknowncmd{x}",
        )


class SafetyTests(unittest.TestCase):
    def test_plain_vietnamese_is_untouched(self):
        for text in (
            "Chào cậu, hôm nay nghe nhạc gì?",
            "Dùng `/play` rồi vào voice nha.",
            "",
        ):
            self.assertEqual(latex_to_discord(text), text)

    def test_windows_path_and_snake_case_survive(self):
        raw = r"File ở C:\Users\binhb\Downloads, biến user_id và snake_case."
        self.assertEqual(latex_to_discord(raw), raw)

    def test_regex_and_markdown_escapes_survive(self):
        self.assertFalse(contains_latex(r"Regex \d+ bắt số."))
        raw = r"Dùng \(\alpha\) và regex \d+ với escape \*đậm\*."
        self.assertEqual(latex_to_discord(raw), r"Dùng α và regex \d+ với escape \*đậm\*.")

    def test_source_code_fence_is_not_rewritten(self):
        raw = (
            "Code:\n"
            "```python\n"
            'pattern = r"\\frac{1}{2}"\n'
            "```\n"
            r"còn \(\alpha\) thì đổi."
        )
        converted = latex_to_discord(raw)
        self.assertIn('pattern = r"\\frac{1}{2}"', converted)
        self.assertTrue(converted.endswith("còn α thì đổi."))

    def test_text_fence_is_rewritten(self):
        raw = "```text\n" + r"\int_0^1 x^2\,dx = \frac{1}{3}" + "\n```"
        self.assertEqual(latex_to_discord(raw), "```text\n∫₀¹ x² dx = 1/3\n```")


class GrokChatIntegrationTests(unittest.TestCase):
    def test_output_text_is_sanitised(self):
        response = SimpleNamespace(
            output_text=r"Kết quả \(l = 12{,}4\,\mathrm{cm}\).",
            output=[],
        )
        self.assertEqual(
            GrokChat._response_text(response),
            "Kết quả l = 12,4 cm.",
        )

    def test_output_blocks_are_sanitised(self):
        response = SimpleNamespace(
            output_text=None,
            output=[
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": r"\(\Delta l = 0{,}5\,\mathrm{mm}\)"}
                    ],
                }
            ],
        )
        self.assertEqual(GrokChat._response_text(response), "Δl = 0,5 mm")

    def test_last_message_text_is_sanitised(self):
        response = SimpleNamespace(
            output_text=None,
            output=[
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Đang tìm..."}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": r"Vậy \(k = 12\)."}],
                },
            ],
        )
        self.assertEqual(
            GrokChat._last_response_message_text(response),
            "Vậy k = 12.",
        )


if __name__ == "__main__":
    unittest.main()
