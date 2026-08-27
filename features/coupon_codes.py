"""Gift-code tracker for the gacha games used by Peto's Discord servers.

Player identifiers are opt-in and scoped to the Discord user who supplied them.
Only Brown Dust 2 supports automatic redemption; it uses the same public BD2
Pulse endpoint as the upstream rapi-bot integration and never stores passwords.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import aiosqlite
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("%s không hợp lệ; dùng mặc định %s", name, default)
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("%s không hợp lệ; dùng mặc định %s", name, default)
        return default


@dataclass(frozen=True)
class CouponGame:
    slug: str
    name: str
    short_name: str
    emoji: str
    color: int
    redemption_hint: str
    redeem_url: str = ""
    account_label: str = "UID"
    supports_auto_redeem: bool = False


@dataclass(frozen=True)
class RedemptionResult:
    success: bool
    code: str
    message: str
    error_code: str = ""

    @property
    def counts_as_redeemed(self) -> bool:
        return self.success or self.error_code == "AlreadyUsed"


GAMES: dict[str, CouponGame] = {
    "brown_dust_2": CouponGame(
        slug="brown_dust_2",
        name="Brown Dust 2",
        short_name="BD2",
        emoji="🎟️",
        color=0x8B4513,
        redemption_hint="Nhập code ở trang redeem chính thức hoặc trong game.",
        redeem_url="https://redeem.bd2.pmang.cloud/bd2/index.html?lang=en-US",
        account_label="nickname",
        supports_auto_redeem=True,
    ),
    "nikke": CouponGame(
        slug="nikke",
        name="GODDESS OF VICTORY: NIKKE",
        short_name="NIKKE",
        emoji="🔫",
        color=0x3498DB,
        redemption_hint="Trong game: Notice (chuông) → CD-Key Redemption Portal.",
        account_label="UID",
    ),
    "blue_archive": CouponGame(
        slug="blue_archive",
        name="Blue Archive",
        short_name="Blue Archive",
        emoji="💙",
        color=0x00BFFF,
        redemption_hint="Cách nhập code tùy khu vực; kiểm tra mục Account/Coupon trong game.",
        account_label="UID",
    ),
}

GAME_ALIASES = {
    "bd2": "brown_dust_2",
    "brown_dust": "brown_dust_2",
    "brown_dust_2": "brown_dust_2",
    "nikke": "nikke",
    "ba": "blue_archive",
    "blue_archive": "blue_archive",
}

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,39}$")
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def normalize_game(value: str) -> str | None:
    key = re.sub(r"[\s-]+", "_", str(value or "").strip().casefold())
    return GAME_ALIASES.get(key)


def normalize_code(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def parse_expiration(value: str | None) -> int | None:
    """Parse YYYY-MM-DD as the end of that UTC day; blank/never means no expiry."""
    raw = str(value or "").strip().casefold()
    if not raw or raw in {"never", "none", "không", "khong"}:
        return None
    match = DATE_RE.fullmatch(raw)
    if not match:
        raise ValueError("Ngày hết hạn phải có dạng YYYY-MM-DD hoặc `never`.")
    try:
        day = datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
            23, 59, 59, tzinfo=UTC,
        )
    except ValueError as error:
        raise ValueError("Ngày hết hạn không tồn tại.") from error
    return int(day.timestamp())


def valid_source_url(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Nguồn phải là link HTTP/HTTPS hợp lệ.")
    return raw[:500]


def coupon_is_active(row: dict, now: int | None = None) -> bool:
    now = int(now if now is not None else time.time())
    expires_at = row.get("expires_at")
    return bool(row.get("active")) and (expires_at is None or int(expires_at) >= now)


class CouponCodes(commands.Cog):
    coupon = app_commands.Group(
        name="coupon",
        description="Gift code Brown Dust 2, NIKKE và Blue Archive",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = Path(
            os.getenv("COUPON_DB", "coupon_codes.db")
        ).resolve()
        self.warning_days = max(1, min(14, _env_int("COUPON_WARNING_DAYS", 3)))
        self._job_lock = asyncio.Lock()
        self._redeem_lock = asyncio.Lock()
        self._last_redeem_request = 0.0
        self.bd2_rate_limit = max(
            0.5, _env_float("COUPON_BD2_RATE_LIMIT_SECONDS", 2.5)
        )
        self.bd2_redeem_url = os.getenv(
            "COUPON_BD2_REDEEM_URL",
            "https://api.thebd2pulse.com/redeem/coupon",
        ).strip()

    async def cog_load(self) -> None:
        await self._init_db()
        self.coupon_loop.start()

    async def cog_unload(self) -> None:
        self.coupon_loop.cancel()

    async def _connect(self) -> aiosqlite.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.db_path)
        if os.name != "nt":
            try:
                self.db_path.chmod(0o600)
            except OSError:
                logger.warning("Không đặt được chmod 600 cho %s", self.db_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        return db

    async def _init_db(self) -> None:
        db = await self._connect()
        try:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS coupons (
                    game TEXT NOT NULL,
                    code TEXT NOT NULL,
                    rewards TEXT NOT NULL DEFAULT '',
                    expires_at INTEGER,
                    source_url TEXT NOT NULL DEFAULT '',
                    added_by INTEGER NOT NULL,
                    added_at INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(game, code)
                );
                CREATE INDEX IF NOT EXISTS idx_coupons_active_expiry
                    ON coupons(active, expires_at);
                CREATE TABLE IF NOT EXISTS coupon_subscriptions (
                    user_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    game_user_id TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'notification-only',
                    new_alerts INTEGER NOT NULL DEFAULT 1,
                    expiry_alerts INTEGER NOT NULL DEFAULT 1,
                    weekly_digest INTEGER NOT NULL DEFAULT 1,
                    subscribed_at INTEGER NOT NULL,
                    dm_disabled INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id, game)
                );
                CREATE TABLE IF NOT EXISTS coupon_used (
                    user_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    code TEXT NOT NULL,
                    used_at INTEGER NOT NULL,
                    PRIMARY KEY(user_id, game, code)
                );
                CREATE TABLE IF NOT EXISTS coupon_notifications (
                    user_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    code TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sent_at INTEGER NOT NULL,
                    PRIMARY KEY(user_id, game, code, kind)
                );
                CREATE TABLE IF NOT EXISTS coupon_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coupon_redemptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    code TEXT NOT NULL,
                    attempted_at INTEGER NOT NULL,
                    success INTEGER NOT NULL,
                    error_code TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_user_game
                    ON coupon_redemptions(user_id, game, attempted_at DESC);
                """
            )
            columns = {
                str(row[1])
                for row in await (await db.execute(
                    "PRAGMA table_info(coupon_subscriptions)"
                )).fetchall()
            }
            if "game_user_id" not in columns:
                await db.execute(
                    "ALTER TABLE coupon_subscriptions "
                    "ADD COLUMN game_user_id TEXT NOT NULL DEFAULT ''"
                )
            if "mode" not in columns:
                await db.execute(
                    "ALTER TABLE coupon_subscriptions "
                    "ADD COLUMN mode TEXT NOT NULL DEFAULT 'notification-only'"
                )
            await db.commit()
        finally:
            await db.close()

    def _game(self, value: str) -> CouponGame | None:
        slug = normalize_game(value)
        return GAMES.get(slug or "")

    async def game_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        del interaction
        needle = current.casefold().strip()
        return [
            app_commands.Choice(name=game.name[:100], value=game.slug)
            for game in GAMES.values()
            if not needle or needle in game.name.casefold() or needle in game.slug
        ][:25]

    async def code_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        game_value = getattr(interaction.namespace, "game", "")
        game = self._game(str(game_value or ""))
        if not game:
            return []
        rows = await self._coupon_rows(game.slug, include_inactive=True)
        needle = normalize_code(current)
        return [
            app_commands.Choice(
                name=f"{row['code']} — {row['rewards']}"[:100], value=row["code"]
            )
            for row in rows
            if not needle or needle in row["code"]
        ][:25]

    async def _require_owner(self, interaction: discord.Interaction) -> bool:
        if await self.bot.is_owner(interaction.user):
            return True
        await interaction.response.send_message(
            "❌ Chỉ chủ bot mới được quản lý kho coupon.", ephemeral=True
        )
        return False

    async def _coupon_rows(
        self,
        game: str,
        *,
        include_inactive: bool = False,
        user_id: int | None = None,
    ) -> list[dict]:
        db = await self._connect()
        try:
            query = "SELECT * FROM coupons WHERE game = ?"
            params: list[object] = [game]
            if not include_inactive:
                query += " AND active = 1 AND (expires_at IS NULL OR expires_at >= ?)"
                params.append(int(time.time()))
            if user_id is not None:
                query += (
                    " AND NOT EXISTS (SELECT 1 FROM coupon_used "
                    "WHERE coupon_used.user_id = ? AND coupon_used.game = coupons.game "
                    "AND coupon_used.code = coupons.code)"
                )
                params.append(user_id)
            query += " ORDER BY expires_at IS NULL, expires_at, added_at DESC"
            cursor = await db.execute(query, params)
            return [dict(row) for row in await cursor.fetchall()]
        finally:
            await db.close()

    async def _subscription_games(self, user_id: int) -> list[str]:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT game FROM coupon_subscriptions WHERE user_id = ? "
                "ORDER BY game", (user_id,),
            )
            return [str(row[0]) for row in await cursor.fetchall()]
        finally:
            await db.close()

    async def _subscription_rows(self, user_id: int) -> list[dict]:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM coupon_subscriptions WHERE user_id = ? ORDER BY game",
                (user_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]
        finally:
            await db.close()

    async def _subscription_row(self, user_id: int, game: str) -> dict | None:
        db = await self._connect()
        try:
            row = await (await db.execute(
                "SELECT * FROM coupon_subscriptions WHERE user_id=? AND game=?",
                (user_id, game),
            )).fetchone()
            return dict(row) if row else None
        finally:
            await db.close()

    async def _set_subscription(
        self,
        user_id: int,
        game: str,
        enabled: bool,
        *,
        game_user_id: str = "",
        mode: str = "notification-only",
    ) -> None:
        if mode not in {"notification-only", "auto-redeem"}:
            raise ValueError("Chế độ coupon không hợp lệ.")
        db = await self._connect()
        try:
            if enabled:
                await db.execute(
                    """
                    INSERT INTO coupon_subscriptions(
                        user_id, game, game_user_id, mode, subscribed_at
                    )
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, game) DO UPDATE SET
                        game_user_id=excluded.game_user_id,
                        mode=excluded.mode,
                        dm_disabled=0
                    """,
                    (user_id, game, game_user_id, mode, int(time.time())),
                )
            else:
                await db.execute(
                    "DELETE FROM coupon_subscriptions WHERE user_id = ? AND game = ?",
                    (user_id, game),
                )
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _coupon_embed(game: CouponGame, row: dict, *, title: str) -> discord.Embed:
        expires_at = row.get("expires_at")
        expiry = (
            f"<t:{int(expires_at)}:F> — <t:{int(expires_at)}:R>"
            if expires_at else "Không ghi thời hạn"
        )
        embed = discord.Embed(
            title=f"{game.emoji} {title}",
            description=f"## `{row['code']}`\n{row.get('rewards') or 'Chưa rõ phần thưởng.'}",
            color=game.color,
        )
        embed.add_field(name="⏰ Hết hạn", value=expiry, inline=False)
        embed.add_field(name="📱 Cách nhập", value=game.redemption_hint, inline=False)
        source = str(row.get("source_url") or "")
        if source:
            embed.add_field(name="🔗 Nguồn", value=f"[Mở thông báo gốc]({source})", inline=False)
        embed.set_footer(
            text="Auto-redeem chỉ chạy khi chính người dùng bật cho hồ sơ Brown Dust 2."
        )
        return embed

    def _channel_id(self, game: CouponGame) -> int:
        return max(0, _env_int(f"COUPON_{game.slug.upper()}_CHANNEL_ID", 0))

    def _role_id(self, game: CouponGame) -> int:
        return max(0, _env_int(f"COUPON_{game.slug.upper()}_ROLE_ID", 0))

    async def _notification_allowed(
        self, user_id: int, game: str, code: str, kind: str
    ) -> bool:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT 1 FROM coupon_notifications WHERE user_id=? AND game=? "
                "AND code=? AND kind=?",
                (user_id, game, code, kind),
            )
            return await cursor.fetchone() is None
        finally:
            await db.close()

    async def _record_notification(
        self, user_id: int, game: str, code: str, kind: str
    ) -> None:
        db = await self._connect()
        try:
            await db.execute(
                "INSERT OR IGNORE INTO coupon_notifications "
                "(user_id, game, code, kind, sent_at) VALUES(?, ?, ?, ?, ?)",
                (user_id, game, code, kind, int(time.time())),
            )
            await db.commit()
        finally:
            await db.close()

    async def _subscribers(
        self, game: str, preference: str, *, mode: str | None = None
    ) -> list[int]:
        if preference not in {"new_alerts", "expiry_alerts", "weekly_digest"}:
            return []
        db = await self._connect()
        try:
            mode_filter = " AND mode=?" if mode else ""
            params: tuple[object, ...] = (game, mode) if mode else (game,)
            cursor = await db.execute(
                f"SELECT user_id FROM coupon_subscriptions WHERE game=? "
                f"AND {preference}=1 AND dm_disabled=0{mode_filter}",
                params,
            )
            return [int(row[0]) for row in await cursor.fetchall()]
        finally:
            await db.close()

    async def _disable_dm(self, user_id: int, game: str) -> None:
        db = await self._connect()
        try:
            await db.execute(
                "UPDATE coupon_subscriptions SET dm_disabled=1 WHERE user_id=? AND game=?",
                (user_id, game),
            )
            await db.commit()
        finally:
            await db.close()

    async def _send_dm(self, user_id: int, game: str, embed: discord.Embed) -> bool:
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(embed=embed)
            return True
        except (discord.Forbidden, discord.NotFound):
            await self._disable_dm(user_id, game)
            return False
        except Exception:
            logger.warning("Không gửi được coupon DM cho user=%s", user_id, exc_info=True)
            return False

    async def _mark_used(self, user_id: int, game: str, code: str) -> None:
        db = await self._connect()
        try:
            await db.execute(
                "INSERT OR REPLACE INTO coupon_used(user_id, game, code, used_at) "
                "VALUES(?, ?, ?, ?)",
                (user_id, game, code, int(time.time())),
            )
            await db.commit()
        finally:
            await db.close()

    async def _record_redemption(
        self, user_id: int, game: str, result: RedemptionResult
    ) -> None:
        db = await self._connect()
        try:
            await db.execute(
                "INSERT INTO coupon_redemptions(user_id, game, code, attempted_at, "
                "success, error_code, message) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    game,
                    result.code,
                    int(time.time()),
                    int(result.success),
                    result.error_code[:60],
                    result.message[:500],
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def _redeem_bd2(self, game_user_id: str, code: str) -> RedemptionResult:
        """Redeem through BD2 Pulse, matching the public rapi-bot integration."""
        messages = {
            "AlreadyUsed": "Tài khoản này đã nhập code trước đó.",
            "InvalidCode": "Code không hợp lệ hoặc không tồn tại.",
            "ExpiredCode": "Code đã hết hạn.",
            "ExceededUses": "Code đã đạt giới hạn lượt sử dụng.",
            "UnavailableCode": "Code hiện không khả dụng.",
            "IncorrectUser": "Không tìm thấy nickname Brown Dust 2 này.",
            "ValidationFailed": "Nickname hoặc code không hợp lệ.",
            "ClaimRewardsFailed": "Game chưa chuyển được phần thưởng; hãy thử lại sau.",
            "RateLimited": "Dịch vụ đang giới hạn lượt thử; hãy thử lại sau.",
        }
        payload = {
            "appId": "bd2-live",
            "userId": game_user_id.strip(),
            "code": normalize_code(code),
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Referer": "https://thebd2pulse.com/",
            "Origin": "https://thebd2pulse.com",
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with self._redeem_lock:
            remaining = self.bd2_rate_limit - (
                time.monotonic() - self._last_redeem_request
            )
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_redeem_request = time.monotonic()
            for attempt in range(2):
                try:
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(
                            self.bd2_redeem_url, json=payload, headers=headers
                        ) as response:
                            if response.status == 429 or response.status >= 500:
                                if attempt == 0:
                                    await asyncio.sleep(1.5)
                                    continue
                            if response.status != 200:
                                return RedemptionResult(
                                    False,
                                    payload["code"],
                                    f"Dịch vụ redeem trả HTTP {response.status}.",
                                    "NetworkError",
                                )
                            try:
                                data = await response.json(content_type=None)
                            except (aiohttp.ContentTypeError, ValueError):
                                return RedemptionResult(
                                    False,
                                    payload["code"],
                                    "Dịch vụ redeem trả dữ liệu không hợp lệ.",
                                    "NetworkError",
                                )
                except (aiohttp.ClientError, TimeoutError):
                    if attempt == 0:
                        await asyncio.sleep(1.5)
                        continue
                    return RedemptionResult(
                        False,
                        payload["code"],
                        "Không kết nối được dịch vụ redeem Brown Dust 2.",
                        "NetworkError",
                    )
                if bool(data.get("success")):
                    return RedemptionResult(
                        True,
                        payload["code"],
                        "Đã nhập code thành công; hãy kiểm tra hòm thư trong game.",
                    )
                error_code = str(data.get("error") or "Unknown")
                return RedemptionResult(
                    False,
                    payload["code"],
                    messages.get(error_code, f"Redeem thất bại: {error_code}"),
                    error_code,
                )
        return RedemptionResult(
            False, payload["code"], "Redeem thất bại.", "NetworkError"
        )

    @staticmethod
    def _redemption_embed(
        game: CouponGame, result: RedemptionResult, game_user_id: str
    ) -> discord.Embed:
        if result.success:
            title, color = "✅ Đã tự động nhập code", 0x57F287
        elif result.error_code == "AlreadyUsed":
            title, color = "☑️ Code đã được nhập trước đó", 0xFEE75C
        else:
            title, color = "❌ Không nhập được code", 0xED4245
        embed = discord.Embed(
            title=f"{title} • {game.short_name}",
            description=f"**`{result.code}`**\n{result.message}",
            color=color,
        )
        embed.add_field(
            name=game.account_label.capitalize(), value=f"`{game_user_id}`", inline=False
        )
        embed.set_footer(
            text="Nickname/UID chỉ được lưu cho chính hồ sơ coupon Discord của bạn."
        )
        return embed

    async def _redeem_for_user(
        self,
        user_id: int,
        game: CouponGame,
        game_user_id: str,
        code: str,
        *,
        notify: bool,
    ) -> RedemptionResult:
        if game.slug != "brown_dust_2" or not game.supports_auto_redeem:
            result = RedemptionResult(
                False,
                normalize_code(code),
                f"{game.name} chưa có API auto-redeem.",
                "Unsupported",
            )
        else:
            result = await self._redeem_bd2(game_user_id, code)
        await self._record_redemption(user_id, game.slug, result)
        if result.counts_as_redeemed:
            await self._mark_used(user_id, game.slug, result.code)
        elif result.error_code == "IncorrectUser":
            db = await self._connect()
            try:
                await db.execute(
                    "UPDATE coupon_subscriptions SET mode='notification-only' "
                    "WHERE user_id=? AND game=?",
                    (user_id, game.slug),
                )
                await db.commit()
            finally:
                await db.close()
        if notify:
            await self._send_dm(
                user_id,
                game.slug,
                self._redemption_embed(game, result, game_user_id),
            )
        return result

    async def _auto_redeem_new_coupon(self, row: dict) -> tuple[int, int, int]:
        game = GAMES[row["game"]]
        if not game.supports_auto_redeem:
            return 0, 0, 0
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT user_id, game_user_id, dm_disabled FROM coupon_subscriptions "
                "WHERE game=? AND mode='auto-redeem' AND game_user_id<>''",
                (game.slug,),
            )
            subscribers = [dict(item) for item in await cursor.fetchall()]
        finally:
            await db.close()
        success = already = failed = 0
        for subscription in subscribers:
            result = await self._redeem_for_user(
                int(subscription["user_id"]),
                game,
                str(subscription["game_user_id"]),
                row["code"],
                notify=not bool(subscription["dm_disabled"]),
            )
            if result.success:
                success += 1
            elif result.error_code == "AlreadyUsed":
                already += 1
            else:
                failed += 1
            await asyncio.sleep(0.25)
        return success, already, failed

    async def _announce_coupon(self, row: dict, *, force: bool = False) -> tuple[int, int]:
        game = GAMES[row["game"]]
        embed = self._coupon_embed(game, row, title=f"Code {game.short_name} mới")
        sent = 0
        failed = 0
        channel_id = self._channel_id(game)
        if channel_id and (force or await self._notification_allowed(0, game.slug, row["code"], "channel")):
            channel = self.bot.get_channel(channel_id)
            if channel is not None:
                role_id = self._role_id(game)
                content = f"<@&{role_id}>" if role_id else None
                try:
                    await channel.send(
                        content=content,
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(
                            everyone=False, users=False, roles=True
                        ),
                    )
                    await self._record_notification(0, game.slug, row["code"], "channel")
                except Exception:
                    logger.warning("Không gửi được coupon vào channel=%s", channel_id, exc_info=True)
        for user_id in await self._subscribers(
            game.slug, "new_alerts", mode="notification-only"
        ):
            if not force and not await self._notification_allowed(
                user_id, game.slug, row["code"], "new"
            ):
                continue
            if await self._send_dm(user_id, game.slug, embed):
                sent += 1
                await self._record_notification(user_id, game.slug, row["code"], "new")
            else:
                failed += 1
            await asyncio.sleep(0.15)
        return sent, failed

    @coupon.command(name="codes", description="Xem các gift code đang hoạt động")
    @app_commands.describe(game="Tên game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def codes(self, interaction: discord.Interaction, game: str) -> None:
        selected = self._game(game)
        if not selected:
            return await interaction.response.send_message(
                "❌ Game này chưa được hệ thống coupon hỗ trợ.", ephemeral=True
            )
        rows = await self._coupon_rows(selected.slug, user_id=interaction.user.id)
        if not rows:
            return await interaction.response.send_message(
                f"ℹ️ Chưa có code **{selected.name}** đang hoạt động trong kho Peto.",
                ephemeral=True,
            )
        lines = []
        for row in rows[:20]:
            expiry = f" — hết hạn <t:{row['expires_at']}:R>" if row["expires_at"] else ""
            lines.append(f"**`{row['code']}`**{expiry}\n{row['rewards']}")
        embed = discord.Embed(
            title=f"{selected.emoji} Gift code {selected.name}",
            description="\n\n".join(lines)[:4000],
            color=selected.color,
        )
        if selected.redeem_url:
            embed.url = selected.redeem_url
        embed.set_footer(text=f"{len(rows)} code đang hoạt động • Dữ liệu do chủ bot quản lý")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @coupon.command(name="subscribe", description="Theo dõi code hoặc bật auto-redeem BD2")
    @app_commands.describe(
        game="Tên game",
        account_id="Nickname Brown Dust 2 hoặc UID game",
        auto_redeem="Tự nhập code (hiện chỉ hỗ trợ Brown Dust 2)",
    )
    @app_commands.autocomplete(game=game_autocomplete)
    async def subscribe(
        self,
        interaction: discord.Interaction,
        game: str,
        account_id: str = "",
        auto_redeem: bool = False,
    ) -> None:
        selected = self._game(game)
        if not selected:
            return await interaction.response.send_message(
                "❌ Game này chưa được hệ thống coupon hỗ trợ.", ephemeral=True
            )
        normalized_account = str(account_id or "").strip()
        if len(normalized_account) > 64 or any(
            character in normalized_account for character in "\r\n`"
        ):
            return await interaction.response.send_message(
                f"❌ {selected.account_label.capitalize()} không hợp lệ hoặc quá dài.",
                ephemeral=True,
            )
        if auto_redeem and not selected.supports_auto_redeem:
            return await interaction.response.send_message(
                f"❌ **{selected.name}** chưa có API auto-redeem; bạn vẫn có thể "
                "đăng ký chế độ thông báo.",
                ephemeral=True,
            )
        if auto_redeem and not normalized_account:
            return await interaction.response.send_message(
                f"❌ Cần nhập **{selected.account_label}** để Peto gửi code vào đúng tài khoản.",
                ephemeral=True,
            )
        mode = "auto-redeem" if auto_redeem else "notification-only"
        if auto_redeem:
            await interaction.response.defer(ephemeral=True, thinking=True)
        await self._set_subscription(
            interaction.user.id,
            selected.slug,
            True,
            game_user_id=normalized_account,
            mode=mode,
        )
        if not auto_redeem:
            suffix = (
                f" Hồ sơ {selected.account_label}: `{normalized_account}`."
                if normalized_account else ""
            )
            return await interaction.response.send_message(
                f"📬 Đã đăng ký gift code **{selected.name}** ở chế độ thông báo.{suffix}",
                ephemeral=True,
            )
        rows = await self._coupon_rows(
            selected.slug, user_id=interaction.user.id
        )
        results: list[RedemptionResult] = []
        for row in rows:
            result = await self._redeem_for_user(
                interaction.user.id,
                selected,
                normalized_account,
                row["code"],
                notify=False,
            )
            results.append(result)
            if result.error_code == "IncorrectUser":
                break
            await asyncio.sleep(0.25)
        success = sum(result.success for result in results)
        already = sum(result.error_code == "AlreadyUsed" for result in results)
        failed = len(results) - success - already
        detail = ""
        if results and failed:
            first_error = next(
                (result.message for result in results if not result.counts_as_redeemed),
                "",
            )
            detail = f"\nLỗi đầu tiên: {first_error}"
        invalid_account = any(
            result.error_code == "IncorrectUser" for result in results
        )
        mode_notice = (
            "\n⚠️ Nickname không hợp lệ nên Peto đã chuyển hồ sơ về chế độ thông báo."
            if invalid_account else ""
        )
        await interaction.followup.send(
            f"🤖 Đã cấu hình auto-redeem **{selected.name}** cho "
            f"{selected.account_label} `{normalized_account}`.\n"
            f"Code hiện tại — thành công: `{success}`, đã nhập trước đó: `{already}`, "
            f"lỗi: `{failed}`.{detail}{mode_notice}",
            ephemeral=True,
        )

    @coupon.command(name="unsubscribe", description="Tắt thông báo gift code qua DM")
    @app_commands.describe(game="Tên game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def unsubscribe(self, interaction: discord.Interaction, game: str) -> None:
        selected = self._game(game)
        if not selected:
            return await interaction.response.send_message(
                "❌ Game này chưa được hệ thống coupon hỗ trợ.", ephemeral=True
            )
        await self._set_subscription(interaction.user.id, selected.slug, False)
        await interaction.response.send_message(
            f"🔕 Đã tắt gift code **{selected.name}**.", ephemeral=True
        )

    @coupon.command(name="subscriptions", description="Xem các game đã đăng ký code DM")
    async def subscriptions(self, interaction: discord.Interaction) -> None:
        rows = await self._subscription_rows(interaction.user.id)
        if not rows:
            text = "Bạn chưa đăng ký thông báo gift code game nào."
        else:
            text = "📬 Đang theo dõi:\n" + "\n".join(
                f"• {GAMES[row['game']].emoji} **{GAMES[row['game']].name}** — "
                f"`{'auto-redeem' if row['mode'] == 'auto-redeem' else 'thông báo'}`"
                + (
                    f" • {GAMES[row['game']].account_label}: `{row['game_user_id']}`"
                    if row["game_user_id"] else ""
                )
                for row in rows if row["game"] in GAMES
            )
        await interaction.response.send_message(text, ephemeral=True)

    @coupon.command(name="history", description="Xem lịch sử auto-redeem của bạn")
    @app_commands.describe(game="Tên game")
    @app_commands.autocomplete(game=game_autocomplete)
    async def history(self, interaction: discord.Interaction, game: str) -> None:
        selected = self._game(game)
        if not selected:
            return await interaction.response.send_message(
                "❌ Game này chưa được hỗ trợ.", ephemeral=True
            )
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT code, attempted_at, success, error_code, message "
                "FROM coupon_redemptions WHERE user_id=? AND game=? "
                "ORDER BY attempted_at DESC, id DESC LIMIT 15",
                (interaction.user.id, selected.slug),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        finally:
            await db.close()
        if not rows:
            return await interaction.response.send_message(
                f"ℹ️ Chưa có lượt auto-redeem nào cho **{selected.name}**.",
                ephemeral=True,
            )
        lines = []
        for row in rows:
            if row["success"]:
                icon = "✅"
            elif row["error_code"] == "AlreadyUsed":
                icon = "☑️"
            else:
                icon = "❌"
            lines.append(
                f"{icon} **`{row['code']}`** • <t:{row['attempted_at']}:R>\n"
                f"{row['message']}"
            )
        embed = discord.Embed(
            title=f"🧾 Lịch sử redeem {selected.name}",
            description="\n\n".join(lines)[:4000],
            color=selected.color,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @coupon.command(name="preferences", description="Đổi loại thông báo coupon qua DM")
    @app_commands.describe(
        game="Tên game",
        new_codes="Nhận code mới",
        expiring="Cảnh báo code sắp hết hạn",
        digest="Bản tổng hợp Chủ nhật",
    )
    @app_commands.autocomplete(game=game_autocomplete)
    async def preferences(
        self,
        interaction: discord.Interaction,
        game: str,
        new_codes: bool | None = None,
        expiring: bool | None = None,
        digest: bool | None = None,
    ) -> None:
        selected = self._game(game)
        if not selected:
            return await interaction.response.send_message(
                "❌ Game này chưa được hệ thống coupon hỗ trợ.", ephemeral=True
            )
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM coupon_subscriptions WHERE user_id=? AND game=?",
                (interaction.user.id, selected.slug),
            )
            row = await cursor.fetchone()
            if not row:
                return await interaction.response.send_message(
                    "❌ Hãy dùng `/coupon subscribe` cho game này trước.", ephemeral=True
                )
            values = {
                "new_alerts": int(new_codes if new_codes is not None else row["new_alerts"]),
                "expiry_alerts": int(expiring if expiring is not None else row["expiry_alerts"]),
                "weekly_digest": int(digest if digest is not None else row["weekly_digest"]),
            }
            await db.execute(
                "UPDATE coupon_subscriptions SET new_alerts=?, expiry_alerts=?, "
                "weekly_digest=?, dm_disabled=0 WHERE user_id=? AND game=?",
                (*values.values(), interaction.user.id, selected.slug),
            )
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(
            f"⚙️ **{selected.name}** — code mới: `{'bật' if values['new_alerts'] else 'tắt'}`, "
            f"sắp hết hạn: `{'bật' if values['expiry_alerts'] else 'tắt'}`, "
            f"digest: `{'bật' if values['weekly_digest'] else 'tắt'}`.",
            ephemeral=True,
        )

    @coupon.command(name="used", description="Đánh dấu một code đã sử dụng")
    @app_commands.describe(game="Tên game", code="Gift code đã nhập")
    @app_commands.autocomplete(game=game_autocomplete, code=code_autocomplete)
    async def used(self, interaction: discord.Interaction, game: str, code: str) -> None:
        selected = self._game(game)
        normalized = normalize_code(code)
        if not selected or not CODE_RE.fullmatch(normalized):
            return await interaction.response.send_message(
                "❌ Game hoặc code không hợp lệ.", ephemeral=True
            )
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT 1 FROM coupons WHERE game=? AND code=?", (selected.slug, normalized)
            )
            if not await cursor.fetchone():
                return await interaction.response.send_message(
                    "❌ Code này không có trong kho Peto.", ephemeral=True
                )
        finally:
            await db.close()
        await self._mark_used(interaction.user.id, selected.slug, normalized)
        await interaction.response.send_message(
            f"✅ Đã đánh dấu `{normalized}` là đã dùng; Peto sẽ không nhắc code này nữa.",
            ephemeral=True,
        )

    @coupon.command(name="add", description="[Chủ bot] Thêm và thông báo một gift code")
    @app_commands.describe(
        game="Tên game", code="Gift code", rewards="Phần thưởng",
        expiration="YYYY-MM-DD hoặc never", source="Link nguồn chính thức/cộng đồng",
    )
    @app_commands.autocomplete(game=game_autocomplete)
    async def add(
        self,
        interaction: discord.Interaction,
        game: str,
        code: str,
        rewards: str,
        expiration: str = "never",
        source: str = "",
    ) -> None:
        if not await self._require_owner(interaction):
            return
        selected = self._game(game)
        normalized = normalize_code(code)
        if not selected:
            return await interaction.response.send_message(
                "❌ Game này chưa được hỗ trợ.", ephemeral=True
            )
        if not CODE_RE.fullmatch(normalized):
            return await interaction.response.send_message(
                "❌ Code chỉ được chứa A-Z, 0-9, `_`, `-` và dài 2–40 ký tự.",
                ephemeral=True,
            )
        try:
            expires_at = parse_expiration(expiration)
            source_url = valid_source_url(source)
        except ValueError as error:
            return await interaction.response.send_message(f"❌ {error}", ephemeral=True)
        if expires_at is not None and expires_at < int(time.time()):
            return await interaction.response.send_message(
                "❌ Ngày hết hạn đã nằm trong quá khứ.", ephemeral=True
            )
        reward_text = str(rewards or "").strip()[:500]
        if not reward_text:
            return await interaction.response.send_message(
                "❌ Cần ghi phần thưởng của code.", ephemeral=True
            )
        db = await self._connect()
        try:
            await db.execute(
                """
                INSERT INTO coupons(game, code, rewards, expires_at, source_url,
                                    added_by, added_at, active)
                VALUES(?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(game, code) DO UPDATE SET
                    rewards=excluded.rewards, expires_at=excluded.expires_at,
                    source_url=excluded.source_url, active=1
                """,
                (
                    selected.slug, normalized, reward_text, expires_at, source_url,
                    interaction.user.id, int(time.time()),
                ),
            )
            await db.commit()
        finally:
            await db.close()
        row = {
            "game": selected.slug, "code": normalized, "rewards": reward_text,
            "expires_at": expires_at, "source_url": source_url, "active": 1,
        }
        await interaction.response.defer(ephemeral=True, thinking=True)
        sent, failed = await self._announce_coupon(row)
        auto_success, auto_already, auto_failed = await self._auto_redeem_new_coupon(row)
        await interaction.followup.send(
            f"✅ Đã lưu `{normalized}` cho **{selected.name}**. "
            f"DM thông báo: `{sent}`, lỗi/tắt DM: `{failed}`.\n"
            f"Auto-redeem — thành công: `{auto_success}`, đã nhập: `{auto_already}`, "
            f"lỗi: `{auto_failed}`.",
            ephemeral=True,
        )

    @coupon.command(name="remove", description="[Chủ bot] Ngừng hiển thị một gift code")
    @app_commands.describe(game="Tên game", code="Gift code cần tắt")
    @app_commands.autocomplete(game=game_autocomplete, code=code_autocomplete)
    async def remove(
        self, interaction: discord.Interaction, game: str, code: str
    ) -> None:
        if not await self._require_owner(interaction):
            return
        selected = self._game(game)
        normalized = normalize_code(code)
        if not selected:
            return await interaction.response.send_message("❌ Game không hợp lệ.", ephemeral=True)
        db = await self._connect()
        try:
            cursor = await db.execute(
                "UPDATE coupons SET active=0 WHERE game=? AND code=? AND active=1",
                (selected.slug, normalized),
            )
            await db.commit()
            changed = cursor.rowcount > 0
        finally:
            await db.close()
        await interaction.response.send_message(
            f"{'✅ Đã tắt' if changed else 'ℹ️ Không tìm thấy code đang hoạt động'} "
            f"`{normalized}`.",
            ephemeral=True,
        )

    @coupon.command(name="status", description="[Chủ bot] Xem trạng thái hệ thống coupon")
    async def status(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        db = await self._connect()
        try:
            coupons = int((await (await db.execute(
                "SELECT COUNT(*) FROM coupons WHERE active=1 AND "
                "(expires_at IS NULL OR expires_at>=?)", (int(time.time()),)
            )).fetchone())[0])
            subscribers = int((await (await db.execute(
                "SELECT COUNT(*) FROM coupon_subscriptions"
            )).fetchone())[0])
            disabled = int((await (await db.execute(
                "SELECT COUNT(*) FROM coupon_subscriptions WHERE dm_disabled=1"
            )).fetchone())[0])
            auto = int((await (await db.execute(
                "SELECT COUNT(*) FROM coupon_subscriptions WHERE mode='auto-redeem'"
            )).fetchone())[0])
        finally:
            await db.close()
        channels = ", ".join(
            f"{game.short_name}: `{self._channel_id(game) or 'DM only'}`"
            for game in GAMES.values()
        )
        await interaction.response.send_message(
            "🎟️ **Coupon Health**\n"
            f"• Code hoạt động: `{coupons}`\n"
            f"• Đăng ký: `{subscribers}`\n"
            f"• Auto-redeem BD2: `{auto}`\n"
            f"• DM bị tắt/lỗi: `{disabled}`\n"
            f"• Cảnh báo trước: `{self.warning_days}` ngày\n"
            f"• Kênh: {channels}",
            ephemeral=True,
        )

    async def _expire_old_codes(self) -> int:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "UPDATE coupons SET active=0 WHERE active=1 AND expires_at IS NOT NULL "
                "AND expires_at < ?", (int(time.time()),),
            )
            await db.commit()
            return max(0, cursor.rowcount)
        finally:
            await db.close()

    async def _send_expiry_warnings(self) -> int:
        now = int(time.time())
        deadline = now + self.warning_days * 86400
        sent = 0
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM coupons WHERE active=1 AND expires_at BETWEEN ? AND ?",
                (now, deadline),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        finally:
            await db.close()
        for row in rows:
            game = GAMES.get(row["game"])
            if not game:
                continue
            embed = self._coupon_embed(game, row, title=f"Code {game.short_name} sắp hết hạn")
            for user_id in await self._subscribers(game.slug, "expiry_alerts"):
                db = await self._connect()
                try:
                    used = await (await db.execute(
                        "SELECT 1 FROM coupon_used WHERE user_id=? AND game=? AND code=?",
                        (user_id, game.slug, row["code"]),
                    )).fetchone()
                finally:
                    await db.close()
                if used or not await self._notification_allowed(
                    user_id, game.slug, row["code"], "expiry"
                ):
                    continue
                if await self._send_dm(user_id, game.slug, embed):
                    sent += 1
                    await self._record_notification(user_id, game.slug, row["code"], "expiry")
                await asyncio.sleep(0.15)
        return sent

    async def _meta(self, key: str) -> str:
        db = await self._connect()
        try:
            row = await (await db.execute(
                "SELECT value FROM coupon_meta WHERE key=?", (key,)
            )).fetchone()
            return str(row[0]) if row else ""
        finally:
            await db.close()

    async def _set_meta(self, key: str, value: str) -> None:
        db = await self._connect()
        try:
            await db.execute(
                "INSERT INTO coupon_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value),
            )
            await db.commit()
        finally:
            await db.close()

    async def _weekly_digest(self) -> int:
        now = datetime.now(UTC)
        if now.weekday() != 6 or now.hour < 12:
            return 0
        period = f"{now.isocalendar().year}-W{now.isocalendar().week}"
        if await self._meta("last_weekly_digest") == period:
            return 0
        sent = 0
        for game in GAMES.values():
            for user_id in await self._subscribers(game.slug, "weekly_digest"):
                rows = await self._coupon_rows(game.slug, user_id=user_id)
                if not rows:
                    continue
                description = "\n".join(
                    f"• **`{row['code']}`** — {row['rewards'][:120]}"
                    for row in rows[:15]
                )
                embed = discord.Embed(
                    title=f"📬 Tổng hợp code {game.name} tuần này",
                    description=description[:4000],
                    color=game.color,
                )
                embed.set_footer(
                    text="Các code bạn đã đánh dấu /coupon used đã được ẩn."
                )
                if await self._send_dm(user_id, game.slug, embed):
                    sent += 1
                await asyncio.sleep(0.15)
        await self._set_meta("last_weekly_digest", period)
        return sent

    async def _scheduled_auto_redemptions(self) -> tuple[int, int, int]:
        """Retry pending BD2 codes at most once per six-hour UTC window."""
        period = str(int(time.time()) // (6 * 3600))
        if await self._meta("last_auto_redeem_window") == period:
            return 0, 0, 0
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT user_id, game_user_id, dm_disabled FROM coupon_subscriptions "
                "WHERE game='brown_dust_2' AND mode='auto-redeem' "
                "AND game_user_id<>''"
            )
            subscribers = [dict(row) for row in await cursor.fetchall()]
        finally:
            await db.close()
        success = already = failed = 0
        game = GAMES["brown_dust_2"]
        for subscription in subscribers:
            user_id = int(subscription["user_id"])
            game_user_id = str(subscription["game_user_id"])
            rows = await self._coupon_rows(game.slug, user_id=user_id)
            for row in rows:
                result = await self._redeem_for_user(
                    user_id,
                    game,
                    game_user_id,
                    row["code"],
                    notify=False,
                )
                if result.success:
                    success += 1
                elif result.error_code == "AlreadyUsed":
                    already += 1
                else:
                    failed += 1
                if (
                    not subscription["dm_disabled"]
                    and (result.counts_as_redeemed or result.error_code == "IncorrectUser")
                ):
                    await self._send_dm(
                        user_id,
                        game.slug,
                        self._redemption_embed(game, result, game_user_id),
                    )
                if result.error_code == "IncorrectUser":
                    break
                await asyncio.sleep(0.25)
        await self._set_meta("last_auto_redeem_window", period)
        return success, already, failed

    @tasks.loop(minutes=15)
    async def coupon_loop(self) -> None:
        if self._job_lock.locked():
            return
        async with self._job_lock:
            try:
                expired = await self._expire_old_codes()
                warnings = await self._send_expiry_warnings()
                digest = await self._weekly_digest()
                auto_success, auto_already, auto_failed = (
                    await self._scheduled_auto_redemptions()
                )
                if expired or warnings or digest or auto_success or auto_already or auto_failed:
                    logger.info(
                        "Coupon scheduler: hết hạn=%s, warning DM=%s, digest DM=%s, "
                        "auto thành công=%s, đã nhập=%s, lỗi=%s",
                        expired,
                        warnings,
                        digest,
                        auto_success,
                        auto_already,
                        auto_failed,
                    )
            except Exception:
                logger.exception("Coupon scheduler gặp lỗi; dữ liệu hiện tại vẫn được giữ")

    @coupon_loop.before_loop
    async def before_coupon_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CouponCodes(bot))
