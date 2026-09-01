import unittest
import sqlite3
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from economy_store import (
    AlreadyOwned,
    EconomyDisabled,
    EconomyStore,
    InsufficientExtractionPoints,
    InsufficientPoints,
    VIETNAM_TZ,
    economy_period_keys,
)
from features.economy import (
    Economy,
    build_weekend_event_embed,
    build_weekly_embed,
    reward_multiplier,
    reward_profile,
    weekend_event_status,
)


class EconomyPresentationTests(unittest.TestCase):
    def test_weekday_and_weekend_reward_multipliers_use_utc_plus_7(self):
        monday = datetime(2026, 8, 31, 12, 0, tzinfo=VIETNAM_TZ).timestamp()
        saturday = datetime(2026, 9, 5, 0, 0, tzinfo=VIETNAM_TZ).timestamp()
        sunday = datetime(2026, 9, 6, 23, 59, tzinfo=VIETNAM_TZ).timestamp()

        self.assertEqual(reward_multiplier(monday), 2)
        self.assertEqual(reward_multiplier(saturday), 5)
        self.assertEqual(reward_multiplier(sunday), 5)
        self.assertEqual(
            (
                reward_profile(monday).chat_min,
                reward_profile(monday).chat_max,
                reward_profile(monday).voice_points,
                reward_profile(monday).daily_cap,
            ),
            (16, 24, 10, 1_000),
        )
        self.assertEqual(
            (
                reward_profile(saturday).chat_min,
                reward_profile(saturday).chat_max,
                reward_profile(saturday).voice_points,
                reward_profile(saturday).daily_cap,
            ),
            (40, 60, 25, 2_500),
        )
        self.assertEqual(weekend_event_status(saturday), (True, "2026-09-05"))
        self.assertEqual(weekend_event_status(monday), (False, "2026-08-29"))

    def test_weekend_event_embeds_explain_start_and_end(self):
        started = build_weekend_event_embed(True)
        ended = build_weekend_event_embed(False)
        self.assertEqual(started.title, "📢 Event cuối tuần đang diễn ra")
        self.assertIn("×5 số lượng Peto Points", started.description or "")
        self.assertEqual(ended.title, "📢 Event cuối tuần đã kết thúc")
        self.assertIn("×2", ended.description or "")

    def test_weekly_embed_has_five_safe_rank_lines(self):
        embed = build_weekly_embed(
            "Peto's Server",
            date(2026, 8, 17).isoformat(),
            [(100 + index, 500 - index) for index in range(5)],
        )
        self.assertEqual(embed.title, "🏆 Bảng xếp hạng Peto Points tuần")
        self.assertEqual(len((embed.description or "").splitlines()), 5)
        self.assertIn("<@100>", embed.description or "")
        self.assertEqual(
            embed.footer.text,
            "Peto's Server • Xếp theo điểm đang có.",
        )

    def test_slash_command_surface_is_exposed(self):
        cog = Economy(bot=SimpleNamespace())
        self.assertEqual(cog.points.name, "points")
        self.assertEqual(cog.collection.name, "collection")
        self.assertEqual(cog.rank.name, "rank")
        self.assertEqual(cog.economy.name, "economy")
        child_names = {command.name for command in cog.economy.commands}
        self.assertEqual(
            child_names,
            {"status", "enable", "disable", "channel", "earning", "grant", "preview"},
        )

    def test_weekly_embed_accepts_preview_labels(self):
        embed = build_weekly_embed(
            "Peto's Server",
            date(2026, 8, 17).isoformat(),
            [("Thành viên A", 3_840)],
        )
        self.assertIn("Thành viên A", embed.description or "")


class EconomyStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.store = EconomyStore(Path(self.temp.name) / "economy.db")
        await self.store.init()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_economy_is_disabled_by_default_and_separated_by_guild(self):
        setting = await self.store.get_settings(100)
        self.assertFalse(setting.enabled)
        with self.assertRaises(EconomyDisabled):
            await self.store.award_activity(
                100,
                10,
                amount=10,
                reason="chat",
                source_id="message:1",
                daily_cap=500,
                timestamp=1_800_000_000,
            )

        await self.store.update_settings(100, updated_by=1, enabled=True)
        await self.store.adjust_points(100, 10, delta=200, source_id="seed:1")
        self.assertEqual((await self.store.get_account(100, 10)).balance, 200)
        self.assertEqual((await self.store.get_account(200, 10)).balance, 0)

    async def test_activity_is_idempotent_and_respects_cooldown_and_daily_cap(self):
        await self.store.update_settings(100, updated_by=1, enabled=True)
        now = 1_800_000_000
        first = await self.store.award_activity(
            100,
            10,
            amount=12,
            reason="chat",
            source_id="message:1",
            daily_cap=20,
            timestamp=now,
            chat_cooldown=60,
            content_hash="alpha",
        )
        duplicate = await self.store.award_activity(
            100,
            10,
            amount=12,
            reason="chat",
            source_id="message:1",
            daily_cap=20,
            timestamp=now + 120,
            chat_cooldown=60,
            content_hash="beta",
        )
        cooldown = await self.store.award_activity(
            100,
            10,
            amount=12,
            reason="chat",
            source_id="message:2",
            daily_cap=20,
            timestamp=now + 30,
            chat_cooldown=60,
            content_hash="beta",
        )
        capped = await self.store.award_activity(
            100,
            10,
            amount=12,
            reason="chat",
            source_id="message:3",
            daily_cap=20,
            timestamp=now + 120,
            chat_cooldown=60,
            content_hash="beta",
        )
        self.assertEqual((first, duplicate, cooldown, capped), (12, 0, 0, 8))
        account = await self.store.get_account(100, 10)
        self.assertEqual(account.balance, 20)
        _, week_key = economy_period_keys(now)
        self.assertEqual(await self.store.weekly_points(100, 10, week_key), 20)

    async def test_paid_gacha_is_atomic_and_updates_collection(self):
        await self.store.update_settings(100, updated_by=1, enabled=True)
        await self.store.adjust_points(100, 10, delta=1_300, source_id="seed:1")
        results = [("id3", "Three A"), ("id2", "Two A")] + [
            ("id1", "One A")
        ] * 8
        account = await self.store.record_gacha(
            100,
            10,
            point_cost=1_300,
            results=results,
            source_id="interaction:1",
            timestamp=1_800_000_000,
        )
        self.assertEqual(account.balance, 0)
        self.assertEqual(account.extraction_points, 10)
        self.assertEqual(account.total_pulls, 10)
        summary = await self.store.collection_summary(100, 10)
        self.assertEqual(summary, {"id1": 1, "id2": 1, "id3": 1})
        items, total = await self.store.collection(100, 10, item_kind="id1")
        self.assertEqual(total, 1)
        self.assertEqual(items[0].copies, 8)

    async def test_insufficient_gacha_does_not_mutate_collection(self):
        await self.store.update_settings(100, updated_by=1, enabled=True)
        await self.store.adjust_points(100, 10, delta=129, source_id="seed:1")
        with self.assertRaises(InsufficientPoints):
            await self.store.record_gacha(
                100,
                10,
                point_cost=130,
                results=[("id3", "Three A")],
                source_id="interaction:1",
            )
        self.assertEqual(await self.store.collection_summary(100, 10), {})
        self.assertEqual((await self.store.get_account(100, 10)).balance, 129)

    async def test_exchange_requires_points_and_unowned_identity(self):
        await self.store.update_settings(100, updated_by=1, enabled=True)
        await self.store.adjust_points(100, 10, delta=2_600, source_id="seed:1")
        await self.store.record_gacha(
            100,
            10,
            point_cost=2_600,
            results=[("id1", f"One {index}") for index in range(20)],
            source_id="interaction:1",
        )
        account = await self.store.exchange_item(
            100,
            10,
            item_kind="id3",
            item_name="Three A",
            extraction_cost=20,
            source_id="interaction:2",
        )
        self.assertEqual(account.extraction_points, 0)
        with self.assertRaises(AlreadyOwned):
            await self.store.exchange_item(
                100,
                10,
                item_kind="id3",
                item_name="Three A",
                extraction_cost=20,
                source_id="interaction:3",
            )
        with self.assertRaises(InsufficientExtractionPoints):
            await self.store.exchange_item(
                100,
                10,
                item_kind="id3",
                item_name="Three B",
                extraction_cost=200,
                source_id="interaction:4",
            )

    async def test_collection_rank_counts_unique_items_not_copies(self):
        await self.store.update_settings(100, updated_by=1, enabled=True)
        for user_id, items in (
            (10, [("id3", "A"), ("id3", "A"), ("id1", "B")]),
            (20, [("id2", "C"), ("id1", "D"), ("ego", "E")]),
        ):
            await self.store.adjust_points(
                100, user_id, delta=1_000, source_id=f"seed:{user_id}"
            )
            await self.store.record_gacha(
                100,
                user_id,
                point_cost=100,
                results=items,
                source_id=f"pull:{user_id}",
                timestamp=1_800_000_000 + user_id,
            )
        rows = await self.store.collection_rank(100)
        self.assertEqual(rows[0].user_id, 20)
        self.assertEqual(rows[0].unique_total, 3)
        self.assertEqual(rows[1].unique_total, 2)

    async def test_weekly_top_and_post_marker_are_idempotent(self):
        await self.store.update_settings(100, updated_by=1, enabled=True)
        now = 1_800_000_000
        _, week_key = economy_period_keys(now)
        for user_id, amount in ((10, 20), (20, 50), (30, 35)):
            awarded = await self.store.award_activity(
                100,
                user_id,
                amount=amount,
                reason="voice",
                source_id=f"voice:{user_id}",
                daily_cap=500,
                timestamp=now,
            )
            self.assertEqual(awarded, amount)
        self.assertEqual(
            await self.store.weekly_top(100, week_key, 2),
            [(20, 50), (30, 35)],
        )
        self.assertFalse(await self.store.weekly_posted(100, week_key))
        await self.store.mark_weekly_posted(100, week_key)
        await self.store.mark_weekly_posted(100, week_key)
        self.assertTrue(await self.store.weekly_posted(100, week_key))

    async def test_weekend_event_state_survives_restart_without_duplicates(self):
        self.assertIsNone(await self.store.weekend_event_state(100))
        started = await self.store.set_weekend_event_state(
            100,
            active=True,
            weekend_key="2026-09-05",
            timestamp=1_800_000_000,
        )
        self.assertTrue(started.active)
        reloaded = EconomyStore(self.store.path)
        await reloaded.init()
        persisted = await reloaded.weekend_event_state(100)
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertTrue(persisted.active)
        self.assertEqual(persisted.weekend_key, "2026-09-05")

        ended = await reloaded.set_weekend_event_state(
            100,
            active=False,
            weekend_key="2026-09-05",
            timestamp=1_800_100_000,
        )
        self.assertFalse(ended.active)
        self.assertFalse((await reloaded.weekend_event_state(100)).active)

    async def test_disabling_economy_preserves_balance_and_collection(self):
        await self.store.update_settings(100, updated_by=1, enabled=True)
        await self.store.adjust_points(100, 10, delta=130, source_id="seed:1")
        await self.store.record_gacha(
            100,
            10,
            point_cost=130,
            results=[("id3", "Three A")],
            source_id="interaction:1",
        )
        await self.store.update_settings(100, updated_by=1, enabled=False)
        self.assertEqual((await self.store.get_account(100, 10)).total_pulls, 1)
        self.assertEqual(
            await self.store.collection_summary(100, 10), {"id3": 1}
        )

    async def test_blue_archive_collection_and_pity_are_separate_from_limbus(self):
        await self.store.update_settings(100, updated_by=1, enabled=True)
        await self.store.adjust_points(100, 10, delta=2_600, source_id="seed:multi")
        await self.store.record_gacha(
            100,
            10,
            point_cost=1_300,
            results=[("id3", "Limbus Three")] * 10,
            source_id="pull:limbus",
        )
        account = await self.store.record_gacha(
            100,
            10,
            point_cost=1_300,
            results=[("ba3", "BA Three"), ("ba1", "BA One")],
            source_id="pull:ba",
            game_id="blue_archive",
            banner_id="global:1:2",
            extraction_points_awarded=0,
            recruitment_points_awarded=2,
        )
        self.assertEqual(account.extraction_points, 10)
        self.assertEqual(
            await self.store.collection_summary(100, 10, game_id="limbus"),
            {"id3": 1},
        )
        self.assertEqual(
            await self.store.collection_summary(
                100, 10, game_id="blue_archive"
            ),
            {"ba1": 1, "ba3": 1},
        )
        self.assertEqual(
            await self.store.gacha_pity_points(
                100,
                10,
                game_id="blue_archive",
                banner_id="global:1:2",
            ),
            2,
        )

    async def test_brown_dust_2_pity_counters_commit_with_the_gacha(self):
        await self.store.update_settings(100, updated_by=1, enabled=True)
        await self.store.adjust_points(100, 10, delta=1_300, source_id="seed:bd2")
        await self.store.record_gacha(
            100,
            10,
            point_cost=1_300,
            results=[("bd2_3", "Justia — Knight of Blood")] * 10,
            source_id="pull:bd2",
            game_id="brown_dust_2",
            banner_id="bd2-costume-draw-simulator",
            extraction_points_awarded=0,
            counter_updates={"since_four_star": 4, "since_five_star": 10},
        )
        self.assertEqual(
            await self.store.gacha_counter_values(
                100,
                10,
                game_id="brown_dust_2",
                banner_id="bd2-costume-draw-simulator",
            ),
            {"since_four_star": 4, "since_five_star": 10},
        )
        self.assertEqual(
            await self.store.collection_summary(
                100, 10, game_id="brown_dust_2"
            ),
            {"bd2_3": 1},
        )

    async def test_old_collection_schema_is_migrated_as_limbus(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "old-economy.db"
            db = sqlite3.connect(path)
            try:
                db.execute(
                    """
                    CREATE TABLE economy_collection (
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        item_kind TEXT NOT NULL,
                        item_name TEXT NOT NULL,
                        copies INTEGER NOT NULL DEFAULT 1,
                        first_obtained_at INTEGER NOT NULL,
                        last_obtained_at INTEGER NOT NULL,
                        PRIMARY KEY (guild_id, user_id, item_kind, item_name)
                    )
                    """
                )
                db.execute(
                    "INSERT INTO economy_collection VALUES(1,2,'id3','Old ID',1,10,10)"
                )
                db.commit()
            finally:
                db.close()
            migrated = EconomyStore(path)
            await migrated.init()
            items, total = await migrated.collection(1, 2, game_id="limbus")
            self.assertEqual(total, 1)
            self.assertEqual(items[0].item_name, "Old ID")
            self.assertEqual(items[0].game_id, "limbus")


if __name__ == "__main__":
    unittest.main()
