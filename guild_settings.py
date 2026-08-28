"""Persistent per-guild notification destinations shared by bot features."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GuildNotification:
    guild_id: int
    feature: str
    target: str
    enabled: bool
    channel_id: int | None
    role_id: int | None
    created_at: int
    updated_by: int
    updated_at: int
    legacy: bool = False


class GuildSettingsStore:
    """Small SQLite repository for settings that belong to a Discord guild."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(
            path or os.getenv("GUILD_SETTINGS_DB", "guild_settings.db")
        ).resolve()

    async def _connect(self) -> aiosqlite.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        return db

    async def init(self) -> None:
        db = await self._connect()
        try:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS guild_notification_settings (
                    guild_id INTEGER NOT NULL,
                    feature TEXT NOT NULL,
                    target TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    channel_id INTEGER,
                    role_id INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_by INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, feature, target)
                );
                CREATE INDEX IF NOT EXISTS idx_guild_notification_lookup
                    ON guild_notification_settings(feature, target, enabled);
                """
            )
            await db.commit()
        finally:
            await db.close()
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                logger.warning("Không đặt được chmod 600 cho %s", self.path)

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> GuildNotification:
        return GuildNotification(
            guild_id=int(row["guild_id"]),
            feature=str(row["feature"]),
            target=str(row["target"]),
            enabled=bool(row["enabled"]),
            channel_id=(int(row["channel_id"]) if row["channel_id"] else None),
            role_id=(int(row["role_id"]) if row["role_id"] else None),
            created_at=int(row["created_at"]),
            updated_by=int(row["updated_by"]),
            updated_at=int(row["updated_at"]),
        )

    async def get(
        self,
        guild_id: int,
        feature: str,
        target: str,
    ) -> GuildNotification | None:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM guild_notification_settings "
                "WHERE guild_id=? AND feature=? AND target=?",
                (int(guild_id), feature, target),
            )
            row = await cursor.fetchone()
            return self._from_row(row) if row else None
        finally:
            await db.close()

    async def list_destinations(
        self,
        feature: str,
        target: str,
        *,
        enabled_only: bool = False,
    ) -> list[GuildNotification]:
        suffix = " AND enabled=1 AND channel_id IS NOT NULL" if enabled_only else ""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM guild_notification_settings "
                f"WHERE feature=? AND target=?{suffix} ORDER BY guild_id",
                (feature, target),
            )
            return [self._from_row(row) for row in await cursor.fetchall()]
        finally:
            await db.close()

    async def _ensure(
        self,
        guild_id: int,
        feature: str,
        target: str,
        updated_by: int,
    ) -> None:
        now = int(time.time())
        db = await self._connect()
        try:
            await db.execute(
                """
                INSERT OR IGNORE INTO guild_notification_settings (
                    guild_id, feature, target, enabled, channel_id, role_id,
                    created_at, updated_by, updated_at
                ) VALUES (?, ?, ?, 0, NULL, NULL, ?, ?, ?)
                """,
                (int(guild_id), feature, target, now, int(updated_by), now),
            )
            await db.commit()
        finally:
            await db.close()

    async def _update(
        self,
        guild_id: int,
        feature: str,
        target: str,
        updated_by: int,
        assignment: str,
        value: object,
    ) -> GuildNotification:
        await self._ensure(guild_id, feature, target, updated_by)
        db = await self._connect()
        try:
            await db.execute(
                f"UPDATE guild_notification_settings SET {assignment}=?, "
                "updated_by=?, updated_at=? WHERE guild_id=? AND feature=? AND target=?",
                (
                    value,
                    int(updated_by),
                    int(time.time()),
                    int(guild_id),
                    feature,
                    target,
                ),
            )
            await db.commit()
        finally:
            await db.close()
        result = await self.get(guild_id, feature, target)
        if result is None:
            raise RuntimeError("Không đọc lại được cấu hình vừa lưu")
        return result

    async def set_channel(
        self, guild_id: int, feature: str, target: str, channel_id: int, updated_by: int
    ) -> GuildNotification:
        return await self._update(
            guild_id, feature, target, updated_by, "channel_id", int(channel_id)
        )

    async def set_role(
        self,
        guild_id: int,
        feature: str,
        target: str,
        role_id: int | None,
        updated_by: int,
    ) -> GuildNotification:
        return await self._update(
            guild_id, feature, target, updated_by, "role_id", role_id
        )

    async def set_enabled(
        self,
        guild_id: int,
        feature: str,
        target: str,
        enabled: bool,
        updated_by: int,
    ) -> GuildNotification:
        current = await self.get(guild_id, feature, target)
        if enabled and (current is None or current.channel_id is None):
            raise ValueError("Hãy chọn kênh thông báo trước khi bật.")
        return await self._update(
            guild_id, feature, target, updated_by, "enabled", int(enabled)
        )

    async def clear(
        self,
        guild_id: int,
        feature: str,
        target: str,
        updated_by: int,
    ) -> None:
        """Clear values but keep a disabled tombstone over legacy .env fallback."""
        await self._ensure(guild_id, feature, target, updated_by)
        db = await self._connect()
        try:
            await db.execute(
                "UPDATE guild_notification_settings SET enabled=0, channel_id=NULL, "
                "role_id=NULL, updated_by=?, updated_at=? "
                "WHERE guild_id=? AND feature=? AND target=?",
                (
                    int(updated_by),
                    int(time.time()),
                    int(guild_id),
                    feature,
                    target,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def migrate_legacy(
        self,
        bot,
        feature: str,
        target: str,
        channel_id: int | None,
        role_id: int | None,
    ) -> GuildNotification | None:
        """Import one old .env destination once, without overwriting guild choices."""
        if not channel_id:
            return None
        try:
            channel = bot.get_channel(channel_id)
            if channel is None:
                channel = await bot.fetch_channel(channel_id)
            guild = getattr(channel, "guild", None)
            guild_id = int(guild.id) if guild is not None else 0
        except Exception as error:
            logger.warning(
                "Không migrate được kênh fallback %s cho %s/%s: %s",
                channel_id,
                feature,
                target,
                error,
            )
            return None
        if not guild_id:
            return None
        current = await self.get(guild_id, feature, target)
        if current is not None:
            return current
        await self.set_channel(guild_id, feature, target, channel_id, 0)
        if role_id:
            await self.set_role(guild_id, feature, target, role_id, 0)
        return await self.set_enabled(guild_id, feature, target, True, 0)


async def notification_destinations(
    bot,
    store: GuildSettingsStore,
    feature: str,
    target: str,
    *,
    legacy_channel_id: int | None = None,
    legacy_role_id: int | None = None,
) -> list[GuildNotification]:
    """Return enabled DB destinations plus a non-overridden legacy env target."""
    stored = await store.list_destinations(feature, target)
    destinations = [item for item in stored if item.enabled and item.channel_id]
    configured_guild_ids = {item.guild_id for item in stored}
    if not legacy_channel_id:
        return destinations
    try:
        channel = bot.get_channel(legacy_channel_id)
        if channel is None:
            channel = await bot.fetch_channel(legacy_channel_id)
        guild = getattr(channel, "guild", None)
        guild_id = int(guild.id) if guild is not None else 0
    except Exception as error:
        logger.warning(
            "Không resolve được kênh fallback %s cho %s/%s: %s",
            legacy_channel_id,
            feature,
            target,
            error,
        )
        return destinations
    if not guild_id or guild_id in configured_guild_ids:
        return destinations
    destinations.append(
        GuildNotification(
            guild_id=guild_id,
            feature=feature,
            target=target,
            enabled=True,
            channel_id=int(legacy_channel_id),
            role_id=int(legacy_role_id) if legacy_role_id else None,
            created_at=0,
            updated_by=0,
            updated_at=0,
            legacy=True,
        )
    )
    return destinations
