import unittest
from unittest.mock import patch

import discord
from discord import app_commands

import peto_help
from peto_help_catalog import HELP_CATEGORIES


SECRET = "PRIVATE_DEFAULT_OR_CALLBACK_TOKEN"


def make_tree():
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)

    @app_commands.rename(query="song")
    @app_commands.describe(query="Tên bài hoặc link công khai")
    @app_commands.choices(provider=[app_commands.Choice(name="YouTube", value="PRIVATE_CHOICE_VALUE")])
    async def play(interaction: discord.Interaction, query: str, provider: str = SECRET):
        raise AssertionError("Public help must never execute a command callback")

    async def simple(interaction: discord.Interaction):
        raise AssertionError(SECRET)

    tree.add_command(app_commands.Command(name="play", description="Phát nhạc công khai", callback=play))
    tree.add_command(app_commands.Command(name="help", description="Mở trợ giúp", callback=simple))
    tree.add_command(app_commands.Command(name="resetmemoryglobal", description="OWNER_ONLY_SENTINEL", callback=simple))
    coupon = app_commands.Group(name="coupon", description="Gift code")
    coupon.add_command(app_commands.Command(name="codes", description="Danh sách code công khai", callback=simple))
    coupon.add_command(app_commands.Command(name="add", description="OWNER_ONLY_SENTINEL", callback=simple))
    tree.add_command(coupon)
    return tree


class PetoHelpIntentTests(unittest.TestCase):
    def test_vietnamese_unaccented_and_english_how_to(self):
        examples = (
            "Peto ơi, làm sao mở nhạc?", "cach su dung /play", "Lệnh nào dùng để tạo playlist?",
            "Peto có những tính năng gì?", "Cách dùng /coupon subscribe?", "Hướng dẫn kiếm Peto Points",
            "How do I create a playlist?", "How to play music?", "How can I play music with Peto?",
            "Peto có phát nhạc không?", "Cách chỉnh ảnh?", "Peto chỉ tôi cách xóa nền ảnh?",
            "Bạn làm được gì?", "Cách xem points?",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertTrue(peto_help.is_peto_help_request(text))

    def test_direct_actions_are_not_treated_as_read_only_help(self):
        for text in ("Peto mở nhạc AIZO đi", "Peto bỏ qua bài này", "Tạo ảnh mèo giúp tôi",
                     "Peto hãy nhớ tôi thích mèo", "/play AIZO", "Tải video này giúp tôi",
                     "Peto có thể mở nhạc AIZO giúp tôi không?", "Bạn có thể tạo ảnh mèo giúp tôi không?"):
            with self.subTest(text=text):
                self.assertFalse(peto_help.is_peto_help_request(text))

    def test_gameplay_and_general_conversation_are_not_bot_help(self):
        for text in ("Rupture trong Limbus hoạt động thế nào?", "Gacha trong Genshin có pity bao nhiêu?",
                     "Làm sao kiếm điểm trong game Blue Archive?", "Peto có khỏe không?",
                     "Peto thấy bài này thế nào?", "Hôm nay trời đẹp quá", "2 cộng 2 bằng mấy?"):
            with self.subTest(text=text):
                self.assertFalse(peto_help.is_peto_help_request(text))

    def test_quoted_instructions_in_translation_or_rewrite_are_not_help(self):
        for text in ("Dịch câu 'How to play music with Peto?'", "Rewrite: how do I create a playlist?",
                     "Viết lại câu 'cách dùng /play' cho tự nhiên"):
            with self.subTest(text=text):
                self.assertFalse(peto_help.is_peto_help_request(text))


class PetoHelpContextTests(unittest.TestCase):
    def test_registered_display_parameter_and_public_choice_names_are_used(self):
        tree = make_tree()
        context = peto_help.build_peto_help_context(tree, "Cách dùng /play?")
        registry = context.split("### Trích tài liệu", 1)[0]
        self.assertIn("/play song:<bắt buộc; Tên bài hoặc link công khai>", registry)
        self.assertIn("provider:<tùy chọn; lựa chọn: YouTube>", registry)
        self.assertNotIn("query:<", registry)
        self.assertNotIn(SECRET, context)
        self.assertNotIn("PRIVATE_CHOICE_VALUE", context)
        self.assertNotIn("OWNER_ONLY_SENTINEL", context)

    def test_owner_only_group_sibling_and_unregistered_commands_are_excluded(self):
        context = peto_help.build_peto_help_context(make_tree(), "Cách dùng /coupon?")
        self.assertIn("/coupon codes", context)
        self.assertNotIn("/coupon add", context)
        self.assertNotIn("/coupon subscribe", context)
        self.assertNotIn("/resetmemoryglobal", context)
        self.assertNotIn("OWNER_ONLY_SENTINEL", context)

    def test_shared_help_catalog_changes_are_reflected(self):
        title, _ = HELP_CATEGORIES["commands"]
        with patch.dict(HELP_CATEGORIES, {"commands": (title, "`/play <query>` — PUBLIC_UPDATED_SENTINEL")}):
            context = peto_help.build_peto_help_context(make_tree(), "Cách dùng /play?")
        self.assertIn("PUBLIC_UPDATED_SENTINEL", context)

    def test_context_is_bounded_without_cutting_a_public_instruction_line(self):
        lines = [f"`/play <query>` — public step {index} " + "x" * 100 for index in range(200)]
        title, _ = HELP_CATEGORIES["commands"]
        with patch.dict(HELP_CATEGORIES, {"commands": (title, "\n".join(lines))}):
            context = peto_help.build_peto_help_context(make_tree(), "Cách dùng /play?")
        self.assertLessEqual(len(context), peto_help.MAX_HELP_CONTEXT)
        self.assertIn(lines[0], context)
        for line in context.split("### Trích tài liệu dùng chung với bảng trợ giúp\n", 1)[1].splitlines():
            self.assertIn(line, lines)

    def test_unrelated_chat_returns_no_context_without_touching_tree(self):
        class UnusedTree:
            def walk_commands(self):
                raise AssertionError("Unrelated chat must not inspect the registry")

        self.assertEqual(peto_help.build_peto_help_context(UnusedTree(), "Hôm nay trời đẹp quá"), "")

    def test_study_section_has_actual_usage_not_only_memory_commands(self):
        context = peto_help.build_peto_help_context(make_tree(), "Cách dùng Study Mode?")
        self.assertIn("giải bài này", context)
        self.assertIn("Gợi ý", context)

    def test_points_guidance_includes_activity_requirements_without_admin_commands(self):
        context = peto_help.build_peto_help_context(make_tree(), "Làm sao kiếm Peto Points?")
        self.assertIn("Voice cần ít nhất 2 người", context)
        self.assertIn("giới hạn ngày", context)
        self.assertNotIn("/economy", context)

    def test_answer_cannot_invent_commands_or_private_group_siblings(self):
        tree = make_tree()
        for text in ("Dùng `/skip` nhé", "Dùng `/coupon add` nhé", "Dùng `/coupon nonexistent` nhé"):
            self.assertFalse(peto_help.public_help_answer_supported(tree, text))
        self.assertTrue(peto_help.public_help_answer_supported(tree, "Dùng `/play song:AIZO` nhé"))
        self.assertTrue(peto_help.public_help_answer_supported(tree, "Xem https://example.org/docs"))

    def test_unavailable_registry_is_explicit_and_does_not_invent_commands(self):
        context = peto_help.build_peto_help_context(None, "Cách dùng /play?")
        self.assertIn("Chưa có lệnh công khai phù hợp để xác nhận", context)
        self.assertNotIn("/play <query>", context)
        self.assertNotIn("/play song:", context)

    def test_fallback_lists_public_registered_commands_without_private_siblings(self):
        result = peto_help.peto_help_fallback(make_tree(), "Cách dùng /coupon?")
        self.assertIn("chưa thực hiện thao tác nào", result)
        self.assertIn("`/coupon codes`", result)
        self.assertNotIn("/coupon add", result)
        self.assertNotIn(SECRET, result)


if __name__ == "__main__":
    unittest.main()
