import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

import aiosqlite

from features.coupon_codes import (
    CODE_RE,
    CouponCodes,
    GAMES,
    RedemptionResult,
    coupon_is_active,
    normalize_code,
    normalize_game,
    parse_expiration,
    valid_source_url,
)
from guild_settings import GuildNotification


class CouponParsingTests(unittest.TestCase):
    def test_slash_group_exposes_user_and_owner_commands(self):
        cog = CouponCodes(bot=object())
        names = {command.name for command in cog.coupon.commands}
        self.assertEqual(
            names,
            {
                "codes",
                "subscribe",
                "unsubscribe",
                "subscriptions",
                "history",
                "preferences",
                "used",
                "add",
                "remove",
                "status",
            },
        )

    def test_supported_games_and_aliases(self):
        self.assertEqual(normalize_game("BD2"), "brown_dust_2")
        self.assertEqual(normalize_game("Blue Archive"), "blue_archive")
        self.assertEqual(normalize_game("NIKKE"), "nikke")
        self.assertIsNone(normalize_game("Lost Sword"))
        self.assertNotIn("lost_sword", GAMES)

    def test_coupon_code_is_normalized_and_validated(self):
        code = normalize_code("  peto 2026 ")
        self.assertEqual(code, "PETO2026")
        self.assertIsNotNone(CODE_RE.fullmatch(code))
        self.assertIsNone(CODE_RE.fullmatch("BAD CODE!"))

    def test_expiration_is_end_of_utc_day(self):
        timestamp = parse_expiration("2026-08-31")
        self.assertEqual(
            datetime.fromtimestamp(timestamp, UTC),
            datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC),
        )
        self.assertIsNone(parse_expiration("never"))
        with self.assertRaises(ValueError):
            parse_expiration("2026-02-30")

    def test_source_only_accepts_http_urls(self):
        self.assertEqual(valid_source_url(""), "")
        self.assertEqual(valid_source_url("https://example.com/code"), "https://example.com/code")
        with self.assertRaises(ValueError):
            valid_source_url("file:///secret.txt")

    def test_active_coupon_respects_expiration(self):
        now = 1_000
        self.assertTrue(coupon_is_active({"active": 1, "expires_at": None}, now))
        self.assertTrue(coupon_is_active({"active": 1, "expires_at": now}, now))
        self.assertFalse(coupon_is_active({"active": 1, "expires_at": now - 1}, now))
        self.assertFalse(coupon_is_active({"active": 0, "expires_at": None}, now))


class CouponDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_coupon_retries_only_failed_guild(self):
        first = GuildNotification(
            1, "coupon", "nikke", True, 10, None, 0, 1, 1
        )
        second = GuildNotification(
            2, "coupon", "nikke", True, 20, None, 0, 1, 1
        )
        successful_channel = AsyncMock()
        row = {
            "game": "nikke",
            "code": "PETO2026",
            "rewards": "100 Gems",
            "expires_at": None,
            "source_url": "",
            "active": 1,
            "added_at": int(time.time()),
        }

        with TemporaryDirectory() as directory:
            cog = CouponCodes(bot=object())
            cog.db_path = Path(directory) / "coupons.db"
            await cog._init_db()
            cog._destinations = AsyncMock(return_value=[first, second])

            async def resolve(channel_id):
                if channel_id == 20:
                    raise RuntimeError("missing permissions")
                return successful_channel

            cog._resolve_channel = AsyncMock(side_effect=resolve)

            self.assertEqual(await cog._announce_coupon_channels(row), (1, 1))
            self.assertEqual(await cog._announce_coupon_channels(row), (0, 1))
            successful_channel.send.assert_awaited_once()

    async def test_existing_notification_database_is_migrated(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "coupons.db"
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    """
                    CREATE TABLE coupon_subscriptions (
                        user_id INTEGER NOT NULL,
                        game TEXT NOT NULL,
                        new_alerts INTEGER NOT NULL DEFAULT 1,
                        expiry_alerts INTEGER NOT NULL DEFAULT 1,
                        weekly_digest INTEGER NOT NULL DEFAULT 1,
                        subscribed_at INTEGER NOT NULL,
                        dm_disabled INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(user_id, game)
                    )
                    """
                )
                await db.commit()
            cog = CouponCodes(bot=object())
            cog.db_path = path

            await cog._init_db()

            async with aiosqlite.connect(path) as db:
                columns = {
                    str(row[1])
                    for row in await (await db.execute(
                        "PRAGMA table_info(coupon_subscriptions)"
                    )).fetchall()
                }
            self.assertIn("game_user_id", columns)
            self.assertIn("mode", columns)

    async def test_auto_redeem_profile_and_already_used_are_persisted(self):
        with TemporaryDirectory() as directory:
            cog = CouponCodes(bot=object())
            cog.db_path = Path(directory) / "coupons.db"
            await cog._init_db()
            await cog._set_subscription(
                42,
                "brown_dust_2",
                True,
                game_user_id="PetoPlayer",
                mode="auto-redeem",
            )
            db = await cog._connect()
            try:
                await db.execute(
                    "INSERT INTO coupons(game, code, rewards, expires_at, source_url, "
                    "added_by, added_at, active) VALUES(?, ?, ?, ?, ?, ?, ?, 1)",
                    (
                        "brown_dust_2",
                        "BD2PETO",
                        "100 Dia",
                        None,
                        "",
                        1,
                        int(time.time()),
                    ),
                )
                await db.commit()
            finally:
                await db.close()
            cog._redeem_bd2 = AsyncMock(
                return_value=RedemptionResult(
                    False,
                    "BD2PETO",
                    "Tài khoản này đã nhập code trước đó.",
                    "AlreadyUsed",
                )
            )

            result = await cog._redeem_for_user(
                42,
                GAMES["brown_dust_2"],
                "PetoPlayer",
                "BD2PETO",
                notify=False,
            )

            self.assertTrue(result.counts_as_redeemed)
            self.assertEqual(await cog._coupon_rows("brown_dust_2", user_id=42), [])
            subscription = await cog._subscription_row(42, "brown_dust_2")
            self.assertEqual(subscription["game_user_id"], "PetoPlayer")
            self.assertEqual(subscription["mode"], "auto-redeem")
            db = await cog._connect()
            try:
                count = int((await (await db.execute(
                    "SELECT COUNT(*) FROM coupon_redemptions WHERE user_id=42"
                )).fetchone())[0])
            finally:
                await db.close()
            self.assertEqual(count, 1)

    async def test_used_codes_are_hidden_only_for_that_user(self):
        with TemporaryDirectory() as directory:
            cog = CouponCodes(bot=object())
            cog.db_path = Path(directory) / "coupons.db"
            await cog._init_db()
            db = await cog._connect()
            try:
                await db.execute(
                    "INSERT INTO coupons(game, code, rewards, expires_at, source_url, "
                    "added_by, added_at, active) VALUES(?, ?, ?, ?, ?, ?, ?, 1)",
                    ("nikke", "PETO2026", "100 Gems", None, "", 1, int(time.time())),
                )
                await db.execute(
                    "INSERT INTO coupon_used(user_id, game, code, used_at) VALUES(?, ?, ?, ?)",
                    (10, "nikke", "PETO2026", int(time.time())),
                )
                await db.commit()
            finally:
                await db.close()

            self.assertEqual(len(await cog._coupon_rows("nikke")), 1)
            self.assertEqual(await cog._coupon_rows("nikke", user_id=10), [])
            self.assertEqual(len(await cog._coupon_rows("nikke", user_id=11)), 1)

    async def test_subscriptions_are_persistent_and_removable(self):
        with TemporaryDirectory() as directory:
            cog = CouponCodes(bot=object())
            cog.db_path = Path(directory) / "coupons.db"
            await cog._init_db()

            await cog._set_subscription(42, "blue_archive", True)
            self.assertEqual(await cog._subscription_games(42), ["blue_archive"])
            await cog._set_subscription(42, "blue_archive", False)
            self.assertEqual(await cog._subscription_games(42), [])


if __name__ == "__main__":
    unittest.main()
