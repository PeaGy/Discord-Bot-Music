import unittest
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from features.daily_reset import (
    BASE_GAMES,
    DailyEvent,
    DailyReset,
    build_daily_embed,
    due_events,
    load_games_from_env,
    next_reset_at,
    parse_utc_time,
)


NIKKE = next(game for game in BASE_GAMES if game.slug == "nikke")
BD2 = next(game for game in BASE_GAMES if game.slug == "brown_dust_2")
LIMBUS = next(game for game in BASE_GAMES if game.slug == "limbus_company")


class DailyResetScheduleTests(unittest.TestCase):
    def test_parse_utc_time_rejects_invalid_values(self):
        self.assertEqual(parse_utc_time("19:30", (1, 2)), (19, 30))
        self.assertEqual(parse_utc_time("24:00", (1, 2)), (1, 2))
        self.assertEqual(parse_utc_time("oops", (1, 2)), (1, 2))

    def test_next_reset_moves_to_tomorrow_at_exact_reset(self):
        before = datetime(2026, 8, 27, 19, 59, tzinfo=UTC)
        exact = datetime(2026, 8, 27, 20, 0, tzinfo=UTC)

        self.assertEqual(next_reset_at(NIKKE, before), exact)
        self.assertEqual(
            next_reset_at(NIKKE, exact),
            datetime(2026, 8, 28, 20, 0, tzinfo=UTC),
        )

    def test_warning_for_midnight_reset_belongs_to_next_day(self):
        now = datetime(2026, 8, 27, 23, 1, tzinfo=UTC)
        events = due_events(
            BD2,
            now,
            warning_minutes=60,
            catchup_minutes=5,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "warning")
        self.assertEqual(events[0].reset_at.day, 28)
        self.assertEqual(events[0].scheduled_at.hour, 23)

    def test_event_outside_catchup_window_is_not_due(self):
        events = due_events(
            NIKKE,
            datetime(2026, 8, 27, 20, 6, tzinfo=UTC),
            warning_minutes=60,
            catchup_minutes=5,
        )
        self.assertEqual(events, [])

    def test_reset_embed_contains_checklist_but_warning_does_not(self):
        reset_at = datetime(2026, 8, 27, 20, 0, tzinfo=UTC)
        reset_event = DailyEvent("nikke", "reset", reset_at, reset_at)
        warning_event = DailyEvent(
            "nikke",
            "warning",
            datetime(2026, 8, 27, 19, 0, tzinfo=UTC),
            reset_at,
        )

        self.assertEqual(len(build_daily_embed(NIKKE, reset_event).fields), len(NIKKE.checklist))
        self.assertEqual(len(build_daily_embed(NIKKE, warning_event).fields), 0)

    def test_limbus_reset_is_0600_kst_and_thursday_has_weekly_note(self):
        reset_at = datetime(2026, 8, 26, 21, 0, tzinfo=UTC)
        event = DailyEvent("limbus_company", "reset", reset_at, reset_at)
        embed = build_daily_embed(LIMBUS, event)

        self.assertEqual((LIMBUS.reset_hour, LIMBUS.reset_minute), (21, 0))
        self.assertTrue(
            any(field.name == "Reset tuần (Thứ Năm KST)" for field in embed.fields)
        )

    def test_all_keyword_enables_every_supported_game(self):
        with patch.dict(os.environ, {"DAILY_RESET_GAMES": "all"}):
            games = load_games_from_env()

        self.assertEqual(set(games), {game.slug for game in BASE_GAMES})
        self.assertIn("limbus_company", games)

    def test_short_limbus_alias_is_accepted(self):
        with patch.dict(os.environ, {"DAILY_RESET_GAMES": "limbus"}):
            games = load_games_from_env()

        self.assertEqual(set(games), {"limbus_company"})


class DailyResetStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_event_is_announced_only_once(self):
        with TemporaryDirectory() as directory:
            cog = DailyReset(bot=object())
            cog.db_path = Path(directory) / "daily-reset.db"
            configured = replace(NIKKE, channel_id=123)
            cog.games = {configured.slug: configured}
            cog._announce_event = AsyncMock()
            await cog._init_db()
            now = datetime(2026, 8, 27, 20, 1, tzinfo=UTC)

            self.assertEqual(await cog.check_due_events(now), 1)
            self.assertEqual(await cog.check_due_events(now), 0)
            cog._announce_event.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
