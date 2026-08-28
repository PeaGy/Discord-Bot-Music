import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from commands.settings import (
    NotificationSettingsView,
    Settings,
    TARGET_LABELS,
    can_manage_guild,
)
from guild_settings import GuildSettingsStore, notification_destinations


class _Guild:
    id = 123


class _Channel:
    id = 456
    guild = _Guild()


class _Bot:
    def get_channel(self, channel_id):
        return _Channel() if channel_id == 456 else None


class GuildSettingsStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_manage_server_permission_is_required(self):
        manager = SimpleNamespace(
            user=SimpleNamespace(
                guild_permissions=SimpleNamespace(
                    manage_guild=True,
                    administrator=False,
                )
            )
        )
        member = SimpleNamespace(
            user=SimpleNamespace(
                guild_permissions=SimpleNamespace(
                    manage_guild=False,
                    administrator=False,
                )
            )
        )

        self.assertTrue(can_manage_guild(manager))
        self.assertFalse(can_manage_guild(member))

    async def test_settings_command_covers_every_notification_target(self):
        cog = Settings(bot=object())

        self.assertEqual(
            {command.name for command in cog.settings.commands},
            {"notifications", "ai"},
        )
        self.assertIn("limbus_company", TARGET_LABELS["daily_reset"])
        self.assertIn("brown_dust_2", TARGET_LABELS["coupon"])
        self.assertEqual(
            TARGET_LABELS["projectmoon"],
            {"official_youtube": "ProjectMoon Official"},
        )

    async def test_settings_panel_reflects_saved_channel_role_and_state(self):
        with TemporaryDirectory() as directory:
            cog = Settings(bot=object())
            cog.store = GuildSettingsStore(Path(directory) / "guild-settings.db")
            await cog.store.init()
            await cog.store.set_channel(1, "daily_reset", "limbus_company", 10, 99)
            await cog.store.set_role(1, "daily_reset", "limbus_company", 20, 99)
            await cog.store.set_enabled(1, "daily_reset", "limbus_company", True, 99)
            view = NotificationSettingsView(
                cog, 1, 99, "daily_reset", "limbus_company"
            )
            embed = await view.build_embed()

            self.assertIn("🟢 Đang bật", embed.description)
            self.assertIn("<#10>", embed.description)
            self.assertIn("<@&20>", embed.description)
            self.assertEqual(view.toggle.label, "Tắt thông báo")

    async def test_settings_are_scoped_per_guild_and_target(self):
        with TemporaryDirectory() as directory:
            store = GuildSettingsStore(Path(directory) / "guild-settings.db")
            await store.init()
            await store.set_channel(1, "daily_reset", "limbus_company", 10, 99)
            await store.set_role(1, "daily_reset", "limbus_company", 20, 99)
            await store.set_enabled(1, "daily_reset", "limbus_company", True, 99)
            await store.set_channel(2, "daily_reset", "limbus_company", 30, 88)

            first = await store.get(1, "daily_reset", "limbus_company")
            second = await store.get(2, "daily_reset", "limbus_company")

            self.assertTrue(first.enabled)
            self.assertEqual((first.channel_id, first.role_id), (10, 20))
            self.assertFalse(second.enabled)
            self.assertEqual(second.channel_id, 30)

    async def test_notification_cannot_be_enabled_without_channel(self):
        with TemporaryDirectory() as directory:
            store = GuildSettingsStore(Path(directory) / "guild-settings.db")
            await store.init()

            with self.assertRaisesRegex(ValueError, "chọn kênh"):
                await store.set_enabled(1, "coupon", "nikke", True, 99)

    async def test_explicit_disabled_setting_overrides_legacy_env_destination(self):
        with TemporaryDirectory() as directory:
            store = GuildSettingsStore(Path(directory) / "guild-settings.db")
            await store.init()
            migrated = await store.migrate_legacy(
                _Bot(), "projectmoon", "official_youtube", 456, 789
            )
            self.assertTrue(migrated.enabled)
            self.assertEqual((migrated.channel_id, migrated.role_id), (456, 789))
            await store.clear(123, "projectmoon", "official_youtube", 99)

            destinations = await notification_destinations(
                _Bot(),
                store,
                "projectmoon",
                "official_youtube",
                legacy_channel_id=456,
                legacy_role_id=789,
            )

            self.assertEqual(destinations, [])


if __name__ == "__main__":
    unittest.main()
