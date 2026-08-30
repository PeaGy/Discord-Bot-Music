"""Persistent per-guild Peto Points and multi-game collection storage."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import aiosqlite


VIETNAM_TZ = timezone(timedelta(hours=7))


class EconomyError(RuntimeError):
    """Base error for expected economy failures."""


class EconomyDisabled(EconomyError):
    pass


class InsufficientPoints(EconomyError):
    def __init__(self, balance: int, required: int):
        self.balance = int(balance)
        self.required = int(required)
        super().__init__(f"Cần {required:,} Peto Points nhưng hiện có {balance:,}.")


class InsufficientExtractionPoints(EconomyError):
    def __init__(self, balance: int, required: int):
        self.balance = int(balance)
        self.required = int(required)
        super().__init__(
            f"Cần {required:,} Extraction Points nhưng hiện có {balance:,}."
        )


class AlreadyOwned(EconomyError):
    pass


class DuplicateEconomyAction(EconomyError):
    pass


@dataclass(frozen=True, slots=True)
class GuildEconomySettings:
    guild_id: int
    enabled: bool
    chat_enabled: bool
    voice_enabled: bool
    leaderboard_channel_id: int | None
    created_at: int
    updated_at: int
    updated_by: int


@dataclass(frozen=True, slots=True)
class EconomyAccount:
    guild_id: int
    user_id: int
    balance: int
    extraction_points: int
    lifetime_earned: int
    total_pulls: int


@dataclass(frozen=True, slots=True)
class CollectionItem:
    item_kind: str
    item_name: str
    copies: int
    first_obtained_at: int
    last_obtained_at: int
    game_id: str = "limbus"


@dataclass(frozen=True, slots=True)
class CollectionRank:
    user_id: int
    unique_total: int
    id3: int
    id2: int
    id1: int
    ego: int
    reached_at: int


def economy_period_keys(timestamp: int | float | None = None) -> tuple[str, str]:
    moment = datetime.fromtimestamp(timestamp or time.time(), VIETNAM_TZ)
    day_key = moment.date().isoformat()
    week_start = (moment.date() - timedelta(days=moment.weekday())).isoformat()
    return day_key, week_start


def previous_week_key(timestamp: int | float | None = None) -> str:
    moment = datetime.fromtimestamp(timestamp or time.time(), VIETNAM_TZ)
    current_start = moment.date() - timedelta(days=moment.weekday())
    return (current_start - timedelta(days=7)).isoformat()


class EconomyStore:
    """Small WAL-backed repository shared by the economy and gacha cogs."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("PETO_ECONOMY_DB", "peto_economy.db")).resolve()
        self._write_lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._ready = False

    async def _connect(self) -> aiosqlite.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA foreign_keys=ON")
        return db

    async def init(self) -> None:
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            db = await self._connect()
            try:
                await db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS economy_guild_settings (
                        guild_id INTEGER PRIMARY KEY,
                        enabled INTEGER NOT NULL DEFAULT 0,
                        chat_enabled INTEGER NOT NULL DEFAULT 1,
                        voice_enabled INTEGER NOT NULL DEFAULT 1,
                        leaderboard_channel_id INTEGER,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        updated_by INTEGER NOT NULL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS economy_accounts (
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
                        extraction_points INTEGER NOT NULL DEFAULT 0
                            CHECK(extraction_points >= 0),
                        lifetime_earned INTEGER NOT NULL DEFAULT 0,
                        total_pulls INTEGER NOT NULL DEFAULT 0,
                        last_chat_award_at INTEGER NOT NULL DEFAULT 0,
                        last_chat_hash TEXT NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY (guild_id, user_id)
                    );

                    CREATE TABLE IF NOT EXISTS economy_daily_earnings (
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        day_key TEXT NOT NULL,
                        points INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (guild_id, user_id, day_key)
                    );

                    CREATE TABLE IF NOT EXISTS economy_weekly_earnings (
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        week_start TEXT NOT NULL,
                        points INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (guild_id, user_id, week_start)
                    );
                    CREATE INDEX IF NOT EXISTS idx_economy_weekly_rank
                        ON economy_weekly_earnings(guild_id, week_start, points DESC);

                    CREATE TABLE IF NOT EXISTS economy_ledger (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        currency TEXT NOT NULL,
                        delta INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        UNIQUE (guild_id, user_id, currency, source_id)
                    );

                    CREATE TABLE IF NOT EXISTS economy_gacha_transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        source_id TEXT NOT NULL,
                        pull_count INTEGER NOT NULL,
                        point_cost INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        UNIQUE (guild_id, user_id, source_id)
                    );

                    CREATE TABLE IF NOT EXISTS economy_gacha_results (
                        transaction_id INTEGER NOT NULL,
                        position INTEGER NOT NULL,
                        item_kind TEXT NOT NULL,
                        item_name TEXT NOT NULL,
                        PRIMARY KEY (transaction_id, position),
                        FOREIGN KEY (transaction_id)
                            REFERENCES economy_gacha_transactions(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS economy_collection (
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        item_kind TEXT NOT NULL,
                        item_name TEXT NOT NULL,
                        copies INTEGER NOT NULL DEFAULT 1,
                        first_obtained_at INTEGER NOT NULL,
                        last_obtained_at INTEGER NOT NULL,
                        PRIMARY KEY (guild_id, user_id, item_kind, item_name)
                    );
                    CREATE INDEX IF NOT EXISTS idx_economy_collection_rank
                        ON economy_collection(guild_id, user_id, item_kind);

                    CREATE TABLE IF NOT EXISTS economy_weekly_posts (
                        guild_id INTEGER NOT NULL,
                        week_start TEXT NOT NULL,
                        posted_at INTEGER NOT NULL,
                        PRIMARY KEY (guild_id, week_start)
                    );

                    CREATE TABLE IF NOT EXISTS economy_gacha_pity (
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        game_id TEXT NOT NULL,
                        banner_id TEXT NOT NULL,
                        points INTEGER NOT NULL DEFAULT 0 CHECK(points >= 0),
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY (guild_id, user_id, game_id, banner_id)
                    );
                    """
                )
                # Additive migration for databases created before multi-game
                # gacha.  Existing rows are Limbus by definition.
                for table, column, declaration in (
                    (
                        "economy_gacha_transactions",
                        "game_id",
                        "TEXT NOT NULL DEFAULT 'limbus'",
                    ),
                    (
                        "economy_gacha_transactions",
                        "banner_id",
                        "TEXT NOT NULL DEFAULT ''",
                    ),
                    (
                        "economy_gacha_results",
                        "game_id",
                        "TEXT NOT NULL DEFAULT 'limbus'",
                    ),
                    (
                        "economy_collection",
                        "game_id",
                        "TEXT NOT NULL DEFAULT 'limbus'",
                    ),
                ):
                    columns = await (
                        await db.execute(f"PRAGMA table_info({table})")
                    ).fetchall()
                    if column not in {str(row[1]) for row in columns}:
                        await db.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
                        )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_economy_collection_game_rank "
                    "ON economy_collection(guild_id, game_id, user_id, item_kind)"
                )
                await db.commit()
            finally:
                await db.close()
            if os.name != "nt":
                try:
                    self.path.chmod(0o600)
                except OSError:
                    pass
            self._ready = True

    @staticmethod
    def _settings_from_row(row: aiosqlite.Row) -> GuildEconomySettings:
        return GuildEconomySettings(
            guild_id=int(row["guild_id"]),
            enabled=bool(row["enabled"]),
            chat_enabled=bool(row["chat_enabled"]),
            voice_enabled=bool(row["voice_enabled"]),
            leaderboard_channel_id=(
                int(row["leaderboard_channel_id"])
                if row["leaderboard_channel_id"]
                else None
            ),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            updated_by=int(row["updated_by"]),
        )

    @staticmethod
    def _account_from_row(row: aiosqlite.Row) -> EconomyAccount:
        return EconomyAccount(
            guild_id=int(row["guild_id"]),
            user_id=int(row["user_id"]),
            balance=int(row["balance"]),
            extraction_points=int(row["extraction_points"]),
            lifetime_earned=int(row["lifetime_earned"]),
            total_pulls=int(row["total_pulls"]),
        )

    async def _ensure_settings(self, db: aiosqlite.Connection, guild_id: int) -> None:
        now = int(time.time())
        await db.execute(
            """
            INSERT OR IGNORE INTO economy_guild_settings (
                guild_id, created_at, updated_at
            ) VALUES (?, ?, ?)
            """,
            (int(guild_id), now, now),
        )

    async def _ensure_account(
        self, db: aiosqlite.Connection, guild_id: int, user_id: int
    ) -> None:
        now = int(time.time())
        await db.execute(
            """
            INSERT OR IGNORE INTO economy_accounts (
                guild_id, user_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (int(guild_id), int(user_id), now, now),
        )

    async def get_settings(self, guild_id: int) -> GuildEconomySettings:
        await self.init()
        async with self._write_lock:
            db = await self._connect()
            try:
                await self._ensure_settings(db, guild_id)
                await db.commit()
                row = await (
                    await db.execute(
                        "SELECT * FROM economy_guild_settings WHERE guild_id=?",
                        (int(guild_id),),
                    )
                ).fetchone()
                assert row is not None
                return self._settings_from_row(row)
            finally:
                await db.close()

    async def update_settings(
        self,
        guild_id: int,
        *,
        updated_by: int,
        enabled: bool | None = None,
        chat_enabled: bool | None = None,
        voice_enabled: bool | None = None,
        leaderboard_channel_id: int | None | object = ...,
    ) -> GuildEconomySettings:
        await self.init()
        async with self._write_lock:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                await self._ensure_settings(db, guild_id)
                updates: list[str] = ["updated_at=?", "updated_by=?"]
                values: list[object] = [int(time.time()), int(updated_by)]
                for column, value in (
                    ("enabled", enabled),
                    ("chat_enabled", chat_enabled),
                    ("voice_enabled", voice_enabled),
                ):
                    if value is not None:
                        updates.append(f"{column}=?")
                        values.append(int(bool(value)))
                if leaderboard_channel_id is not ...:
                    updates.append("leaderboard_channel_id=?")
                    values.append(
                        int(leaderboard_channel_id)
                        if leaderboard_channel_id is not None
                        else None
                    )
                values.append(int(guild_id))
                await db.execute(
                    f"UPDATE economy_guild_settings SET {', '.join(updates)} "
                    "WHERE guild_id=?",
                    values,
                )
                await db.commit()
                row = await (
                    await db.execute(
                        "SELECT * FROM economy_guild_settings WHERE guild_id=?",
                        (int(guild_id),),
                    )
                ).fetchone()
                assert row is not None
                return self._settings_from_row(row)
            except Exception:
                await db.rollback()
                raise
            finally:
                await db.close()

    async def enabled_leaderboards(self) -> list[GuildEconomySettings]:
        await self.init()
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    "SELECT * FROM economy_guild_settings "
                    "WHERE enabled=1 AND leaderboard_channel_id IS NOT NULL"
                )
            ).fetchall()
            return [self._settings_from_row(row) for row in rows]
        finally:
            await db.close()

    async def get_account(self, guild_id: int, user_id: int) -> EconomyAccount:
        await self.init()
        async with self._write_lock:
            db = await self._connect()
            try:
                await self._ensure_account(db, guild_id, user_id)
                await db.commit()
                row = await (
                    await db.execute(
                        "SELECT * FROM economy_accounts WHERE guild_id=? AND user_id=?",
                        (int(guild_id), int(user_id)),
                    )
                ).fetchone()
                assert row is not None
                return self._account_from_row(row)
            finally:
                await db.close()

    async def weekly_points(self, guild_id: int, user_id: int, week_key: str) -> int:
        await self.init()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT points FROM economy_weekly_earnings "
                    "WHERE guild_id=? AND user_id=? AND week_start=?",
                    (int(guild_id), int(user_id), week_key),
                )
            ).fetchone()
            return int(row["points"]) if row else 0
        finally:
            await db.close()

    async def award_activity(
        self,
        guild_id: int,
        user_id: int,
        *,
        amount: int,
        reason: str,
        source_id: str,
        daily_cap: int,
        timestamp: int | None = None,
        chat_cooldown: int = 0,
        content_hash: str = "",
    ) -> int:
        """Award points once and return the actual amount after limits."""
        await self.init()
        now = int(timestamp or time.time())
        day_key, week_key = economy_period_keys(now)
        async with self._write_lock:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                await self._ensure_settings(db, guild_id)
                setting = await (
                    await db.execute(
                        "SELECT * FROM economy_guild_settings WHERE guild_id=?",
                        (int(guild_id),),
                    )
                ).fetchone()
                if not setting or not bool(setting["enabled"]):
                    raise EconomyDisabled("Economy chưa được bật trong server này.")
                await self._ensure_account(db, guild_id, user_id)
                account = await (
                    await db.execute(
                        "SELECT * FROM economy_accounts WHERE guild_id=? AND user_id=?",
                        (int(guild_id), int(user_id)),
                    )
                ).fetchone()
                assert account is not None

                duplicate = await (
                    await db.execute(
                        "SELECT 1 FROM economy_ledger "
                        "WHERE guild_id=? AND user_id=? AND currency='points' "
                        "AND source_id=?",
                        (int(guild_id), int(user_id), source_id),
                    )
                ).fetchone()
                if duplicate:
                    await db.rollback()
                    return 0

                if chat_cooldown:
                    last_at = int(account["last_chat_award_at"])
                    if now - last_at < int(chat_cooldown):
                        await db.rollback()
                        return 0
                    if (
                        content_hash
                        and content_hash == str(account["last_chat_hash"] or "")
                        and now - last_at < max(600, int(chat_cooldown))
                    ):
                        await db.rollback()
                        return 0

                daily = await (
                    await db.execute(
                        "SELECT points FROM economy_daily_earnings "
                        "WHERE guild_id=? AND user_id=? AND day_key=?",
                        (int(guild_id), int(user_id), day_key),
                    )
                ).fetchone()
                earned_today = int(daily["points"]) if daily else 0
                granted = max(0, min(int(amount), int(daily_cap) - earned_today))
                if granted <= 0:
                    await db.rollback()
                    return 0

                await db.execute(
                    "INSERT INTO economy_ledger "
                    "(guild_id,user_id,currency,delta,reason,source_id,created_at) "
                    "VALUES(?,?,'points',?,?,?,?)",
                    (int(guild_id), int(user_id), granted, reason, source_id, now),
                )
                chat_sql = ""
                values: list[object] = [granted, granted, now]
                if chat_cooldown:
                    chat_sql = ", last_chat_award_at=?, last_chat_hash=?"
                    values.extend([now, content_hash])
                values.extend([int(guild_id), int(user_id)])
                await db.execute(
                    "UPDATE economy_accounts SET balance=balance+?, "
                    "lifetime_earned=lifetime_earned+?, updated_at=?"
                    f"{chat_sql} WHERE guild_id=? AND user_id=?",
                    values,
                )
                await db.execute(
                    "INSERT INTO economy_daily_earnings "
                    "(guild_id,user_id,day_key,points) VALUES(?,?,?,?) "
                    "ON CONFLICT(guild_id,user_id,day_key) DO UPDATE SET "
                    "points=points+excluded.points",
                    (int(guild_id), int(user_id), day_key, granted),
                )
                await db.execute(
                    "INSERT INTO economy_weekly_earnings "
                    "(guild_id,user_id,week_start,points) VALUES(?,?,?,?) "
                    "ON CONFLICT(guild_id,user_id,week_start) DO UPDATE SET "
                    "points=points+excluded.points",
                    (int(guild_id), int(user_id), week_key, granted),
                )
                await db.commit()
                return granted
            except EconomyDisabled:
                await db.rollback()
                raise
            except Exception:
                await db.rollback()
                raise
            finally:
                await db.close()

    async def record_gacha(
        self,
        guild_id: int,
        user_id: int,
        *,
        point_cost: int,
        results: Sequence[tuple[str, str]],
        source_id: str,
        game_id: str = "limbus",
        banner_id: str = "",
        extraction_points_awarded: int | None = None,
        recruitment_points_awarded: int = 0,
        timestamp: int | None = None,
    ) -> EconomyAccount:
        await self.init()
        now = int(timestamp or time.time())
        async with self._write_lock:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                await self._ensure_settings(db, guild_id)
                setting = await (
                    await db.execute(
                        "SELECT enabled FROM economy_guild_settings WHERE guild_id=?",
                        (int(guild_id),),
                    )
                ).fetchone()
                if not setting or not bool(setting["enabled"]):
                    raise EconomyDisabled("Economy chưa được bật trong server này.")
                await self._ensure_account(db, guild_id, user_id)
                duplicate = await (
                    await db.execute(
                        "SELECT 1 FROM economy_gacha_transactions "
                        "WHERE guild_id=? AND user_id=? AND source_id=?",
                        (int(guild_id), int(user_id), source_id),
                    )
                ).fetchone()
                if duplicate:
                    raise DuplicateEconomyAction("Lượt quay này đã được xử lý.")
                account = await (
                    await db.execute(
                        "SELECT * FROM economy_accounts WHERE guild_id=? AND user_id=?",
                        (int(guild_id), int(user_id)),
                    )
                ).fetchone()
                assert account is not None
                if int(account["balance"]) < int(point_cost):
                    raise InsufficientPoints(int(account["balance"]), int(point_cost))

                extraction_award = (
                    len(results)
                    if extraction_points_awarded is None
                    else max(0, int(extraction_points_awarded))
                )
                recruitment_award = max(0, int(recruitment_points_awarded))
                game_id = str(game_id or "limbus")
                banner_id = str(banner_id or "")
                cursor = await db.execute(
                    "INSERT INTO economy_gacha_transactions "
                    "(guild_id,user_id,source_id,pull_count,point_cost,created_at,game_id,banner_id) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        int(guild_id),
                        int(user_id),
                        source_id,
                        len(results),
                        int(point_cost),
                        now,
                        game_id,
                        banner_id,
                    ),
                )
                transaction_id = int(cursor.lastrowid)
                await db.executemany(
                    "INSERT INTO economy_gacha_results "
                    "(transaction_id,position,item_kind,item_name,game_id) VALUES(?,?,?,?,?)",
                    [
                        (transaction_id, index, kind, name, game_id)
                        for index, (kind, name) in enumerate(results, start=1)
                    ],
                )
                await db.executemany(
                    """
                    INSERT INTO economy_collection (
                        guild_id,user_id,item_kind,item_name,copies,
                        first_obtained_at,last_obtained_at,game_id
                    ) VALUES(?,?,?,?,1,?,?,?)
                    ON CONFLICT(guild_id,user_id,item_kind,item_name) DO UPDATE SET
                        copies=copies+1,
                        last_obtained_at=excluded.last_obtained_at,
                        game_id=excluded.game_id
                    """,
                    [
                        (int(guild_id), int(user_id), kind, name, now, now, game_id)
                        for kind, name in results
                    ],
                )
                await db.execute(
                    "UPDATE economy_accounts SET balance=balance-?, "
                    "extraction_points=extraction_points+?, total_pulls=total_pulls+?, "
                    "updated_at=? WHERE guild_id=? AND user_id=?",
                    (
                        int(point_cost),
                        extraction_award,
                        len(results),
                        now,
                        int(guild_id),
                        int(user_id),
                    ),
                )
                await db.execute(
                    "INSERT INTO economy_ledger "
                    "(guild_id,user_id,currency,delta,reason,source_id,created_at) "
                    "VALUES(?,?,'points',?,'gacha',?,?)",
                    (int(guild_id), int(user_id), -int(point_cost), source_id, now),
                )
                if extraction_award:
                    await db.execute(
                        "INSERT INTO economy_ledger "
                        "(guild_id,user_id,currency,delta,reason,source_id,created_at) "
                        "VALUES(?,?,'extraction',?,'gacha',?,?)",
                        (int(guild_id), int(user_id), extraction_award, source_id, now),
                    )
                if recruitment_award and banner_id:
                    await db.execute(
                        "INSERT INTO economy_gacha_pity "
                        "(guild_id,user_id,game_id,banner_id,points,updated_at) "
                        "VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(guild_id,user_id,game_id,banner_id) DO UPDATE SET "
                        "points=points+excluded.points, updated_at=excluded.updated_at",
                        (
                            int(guild_id),
                            int(user_id),
                            game_id,
                            banner_id,
                            recruitment_award,
                            now,
                        ),
                    )
                await db.commit()
                updated = await (
                    await db.execute(
                        "SELECT * FROM economy_accounts WHERE guild_id=? AND user_id=?",
                        (int(guild_id), int(user_id)),
                    )
                ).fetchone()
                assert updated is not None
                return self._account_from_row(updated)
            except Exception:
                await db.rollback()
                raise
            finally:
                await db.close()

    async def exchange_item(
        self,
        guild_id: int,
        user_id: int,
        *,
        item_kind: str,
        item_name: str,
        extraction_cost: int,
        source_id: str,
        game_id: str = "limbus",
        timestamp: int | None = None,
    ) -> EconomyAccount:
        await self.init()
        now = int(timestamp or time.time())
        async with self._write_lock:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                await self._ensure_settings(db, guild_id)
                setting = await (
                    await db.execute(
                        "SELECT enabled FROM economy_guild_settings WHERE guild_id=?",
                        (int(guild_id),),
                    )
                ).fetchone()
                if not setting or not bool(setting["enabled"]):
                    raise EconomyDisabled("Economy chưa được bật trong server này.")
                await self._ensure_account(db, guild_id, user_id)
                existing = await (
                    await db.execute(
                        "SELECT 1 FROM economy_collection WHERE guild_id=? AND user_id=? "
                        "AND item_kind=? AND item_name=? AND game_id=?",
                        (
                            int(guild_id),
                            int(user_id),
                            item_kind,
                            item_name,
                            str(game_id or "limbus"),
                        ),
                    )
                ).fetchone()
                if existing:
                    raise AlreadyOwned(f"Bạn đã sở hữu **{item_name}**.")
                duplicate = await (
                    await db.execute(
                        "SELECT 1 FROM economy_ledger WHERE guild_id=? AND user_id=? "
                        "AND currency='extraction' AND source_id=?",
                        (int(guild_id), int(user_id), source_id),
                    )
                ).fetchone()
                if duplicate:
                    raise DuplicateEconomyAction("Lượt đổi này đã được xử lý.")
                account = await (
                    await db.execute(
                        "SELECT * FROM economy_accounts WHERE guild_id=? AND user_id=?",
                        (int(guild_id), int(user_id)),
                    )
                ).fetchone()
                assert account is not None
                available = int(account["extraction_points"])
                if available < int(extraction_cost):
                    raise InsufficientExtractionPoints(available, int(extraction_cost))
                await db.execute(
                    "INSERT INTO economy_collection "
                    "(guild_id,user_id,item_kind,item_name,copies,first_obtained_at,last_obtained_at,game_id) "
                    "VALUES(?,?,?,?,1,?,?,?)",
                    (
                        int(guild_id),
                        int(user_id),
                        item_kind,
                        item_name,
                        now,
                        now,
                        str(game_id or "limbus"),
                    ),
                )
                await db.execute(
                    "UPDATE economy_accounts SET extraction_points=extraction_points-?, "
                    "updated_at=? WHERE guild_id=? AND user_id=?",
                    (int(extraction_cost), now, int(guild_id), int(user_id)),
                )
                await db.execute(
                    "INSERT INTO economy_ledger "
                    "(guild_id,user_id,currency,delta,reason,source_id,created_at) "
                    "VALUES(?,?,'extraction',?,'exchange',?,?)",
                    (
                        int(guild_id),
                        int(user_id),
                        -int(extraction_cost),
                        source_id,
                        now,
                    ),
                )
                await db.commit()
                updated = await (
                    await db.execute(
                        "SELECT * FROM economy_accounts WHERE guild_id=? AND user_id=?",
                        (int(guild_id), int(user_id)),
                    )
                ).fetchone()
                assert updated is not None
                return self._account_from_row(updated)
            except Exception:
                await db.rollback()
                raise
            finally:
                await db.close()

    async def owned_names(
        self,
        guild_id: int,
        user_id: int,
        item_kind: str,
        *,
        game_id: str = "limbus",
    ) -> set[str]:
        await self.init()
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    "SELECT item_name FROM economy_collection "
                    "WHERE guild_id=? AND user_id=? AND item_kind=? AND game_id=?",
                    (int(guild_id), int(user_id), item_kind, str(game_id)),
                )
            ).fetchall()
            return {str(row["item_name"]) for row in rows}
        finally:
            await db.close()

    async def collection(
        self,
        guild_id: int,
        user_id: int,
        *,
        game_id: str = "limbus",
        item_kind: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[CollectionItem], int]:
        await self.init()
        db = await self._connect()
        try:
            where = "guild_id=? AND user_id=? AND game_id=?"
            values: list[object] = [int(guild_id), int(user_id), str(game_id)]
            if item_kind:
                where += " AND item_kind=?"
                values.append(item_kind)
            count = await (
                await db.execute(
                    f"SELECT COUNT(*) AS count FROM economy_collection WHERE {where}",
                    values,
                )
            ).fetchone()
            rows = await (
                await db.execute(
                    f"SELECT * FROM economy_collection WHERE {where} "
                    "ORDER BY CASE item_kind WHEN 'id3' THEN 0 WHEN 'ego' THEN 1 "
                    "WHEN 'ba3' THEN 0 WHEN 'ba2' THEN 1 WHEN 'id2' THEN 2 "
                    "WHEN 'ba1' THEN 2 ELSE 3 END, item_name COLLATE NOCASE "
                    "LIMIT ? OFFSET ?",
                    (*values, int(limit), int(offset)),
                )
            ).fetchall()
            return (
                [
                    CollectionItem(
                        item_kind=str(row["item_kind"]),
                        item_name=str(row["item_name"]),
                        copies=int(row["copies"]),
                        first_obtained_at=int(row["first_obtained_at"]),
                        last_obtained_at=int(row["last_obtained_at"]),
                        game_id=str(row["game_id"]),
                    )
                    for row in rows
                ],
                int(count["count"]) if count else 0,
            )
        finally:
            await db.close()

    async def collection_summary(
        self, guild_id: int, user_id: int, *, game_id: str | None = None
    ) -> dict[str, int]:
        await self.init()
        db = await self._connect()
        try:
            where = "guild_id=? AND user_id=?"
            values: list[object] = [int(guild_id), int(user_id)]
            if game_id is not None:
                where += " AND game_id=?"
                values.append(str(game_id))
            rows = await (
                await db.execute(
                    "SELECT item_kind, COUNT(*) AS count FROM economy_collection "
                    f"WHERE {where} GROUP BY item_kind",
                    values,
                )
            ).fetchall()
            return {str(row["item_kind"]): int(row["count"]) for row in rows}
        finally:
            await db.close()

    async def collection_rank(
        self, guild_id: int, limit: int = 5, *, game_id: str = "limbus"
    ) -> list[CollectionRank]:
        await self.init()
        db = await self._connect()
        try:
            kinds = (
                ("ba3", "ba2", "ba1", "__none__")
                if str(game_id) == "blue_archive"
                else ("id3", "id2", "id1", "ego")
            )
            rows = await (
                await db.execute(
                    """
                    SELECT user_id,
                           COUNT(*) AS unique_total,
                           SUM(CASE WHEN item_kind=? THEN 1 ELSE 0 END) AS id3,
                           SUM(CASE WHEN item_kind=? THEN 1 ELSE 0 END) AS id2,
                           SUM(CASE WHEN item_kind=? THEN 1 ELSE 0 END) AS id1,
                           SUM(CASE WHEN item_kind=? THEN 1 ELSE 0 END) AS ego,
                           MAX(first_obtained_at) AS reached_at
                    FROM economy_collection
                    WHERE guild_id=? AND game_id=?
                    GROUP BY user_id
                    ORDER BY unique_total DESC, id3 DESC, ego DESC, id2 DESC,
                             reached_at ASC, user_id ASC
                    LIMIT ?
                    """,
                    (*kinds, int(guild_id), str(game_id), int(limit)),
                )
            ).fetchall()
            return [
                CollectionRank(
                    user_id=int(row["user_id"]),
                    unique_total=int(row["unique_total"]),
                    id3=int(row["id3"]),
                    id2=int(row["id2"]),
                    id1=int(row["id1"]),
                    ego=int(row["ego"]),
                    reached_at=int(row["reached_at"]),
                )
                for row in rows
            ]
        finally:
            await db.close()

    async def gacha_pity_points(
        self,
        guild_id: int,
        user_id: int,
        *,
        game_id: str,
        banner_id: str,
    ) -> int:
        await self.init()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT points FROM economy_gacha_pity "
                    "WHERE guild_id=? AND user_id=? AND game_id=? AND banner_id=?",
                    (int(guild_id), int(user_id), str(game_id), str(banner_id)),
                )
            ).fetchone()
            return int(row["points"]) if row else 0
        finally:
            await db.close()

    async def weekly_top(
        self, guild_id: int, week_key: str, limit: int = 5
    ) -> list[tuple[int, int]]:
        await self.init()
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    "SELECT user_id, points FROM economy_weekly_earnings "
                    "WHERE guild_id=? AND week_start=? "
                    "ORDER BY points DESC, user_id ASC LIMIT ?",
                    (int(guild_id), week_key, int(limit)),
                )
            ).fetchall()
            return [(int(row["user_id"]), int(row["points"])) for row in rows]
        finally:
            await db.close()

    async def weekly_posted(self, guild_id: int, week_key: str) -> bool:
        await self.init()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT 1 FROM economy_weekly_posts WHERE guild_id=? AND week_start=?",
                    (int(guild_id), week_key),
                )
            ).fetchone()
            return row is not None
        finally:
            await db.close()

    async def mark_weekly_posted(self, guild_id: int, week_key: str) -> None:
        await self.init()
        async with self._write_lock:
            db = await self._connect()
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO economy_weekly_posts "
                    "(guild_id,week_start,posted_at) VALUES(?,?,?)",
                    (int(guild_id), week_key, int(time.time())),
                )
                await db.commit()
            finally:
                await db.close()

    async def adjust_points(
        self,
        guild_id: int,
        user_id: int,
        *,
        delta: int,
        source_id: str,
        reason: str = "admin",
    ) -> EconomyAccount:
        await self.init()
        async with self._write_lock:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                await self._ensure_account(db, guild_id, user_id)
                row = await (
                    await db.execute(
                        "SELECT balance FROM economy_accounts WHERE guild_id=? AND user_id=?",
                        (int(guild_id), int(user_id)),
                    )
                ).fetchone()
                assert row is not None
                applied = int(delta)
                if int(row["balance"]) + applied < 0:
                    applied = -int(row["balance"])
                await db.execute(
                    "UPDATE economy_accounts SET balance=balance+?, updated_at=? "
                    "WHERE guild_id=? AND user_id=?",
                    (applied, int(time.time()), int(guild_id), int(user_id)),
                )
                await db.execute(
                    "INSERT INTO economy_ledger "
                    "(guild_id,user_id,currency,delta,reason,source_id,created_at) "
                    "VALUES(?,?,'points',?,?,?,?)",
                    (
                        int(guild_id),
                        int(user_id),
                        applied,
                        reason,
                        source_id,
                        int(time.time()),
                    ),
                )
                await db.commit()
                updated = await (
                    await db.execute(
                        "SELECT * FROM economy_accounts WHERE guild_id=? AND user_id=?",
                        (int(guild_id), int(user_id)),
                    )
                ).fetchone()
                assert updated is not None
                return self._account_from_row(updated)
            except Exception:
                await db.rollback()
                raise
            finally:
                await db.close()


def get_economy_store(bot: object) -> EconomyStore:
    store = getattr(bot, "peto_economy_store", None)
    if isinstance(store, EconomyStore):
        return store
    store = EconomyStore()
    setattr(bot, "peto_economy_store", store)
    return store
