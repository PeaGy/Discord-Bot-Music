"""Bộ nhớ AI tách biệt theo DM hoặc từng Discord server."""

from collections import deque

import aiosqlite

DB_PATH = "bot_memory.db"
LEGACY_SCOPE = "legacy"
DM_SCOPE = "dm"

# Chế độ Ẩn danh chỉ giữ ngữ cảnh trong RAM, không ghi nội dung xuống SQLite.
_anonymous_history: dict[tuple[str, int], deque[dict]] = {}


def scope_for_guild(guild_id: int | None) -> str:
    return DM_SCOPE if guild_id is None else f"guild:{int(guild_id)}"


async def init_db() -> None:
    """Tạo bảng nếu chưa có. Gọi 1 lần lúc bot khởi động (Cog.cog_load)."""
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL cho phép đọc/ghi đồng thời tốt hơn nhiều so với chế độ mặc
        # định - cần thiết vì giờ có _refresh_summary chạy nền (ghi
        # user_summary/user_message_count) trong lúc luồng chính vẫn có
        # thể đang ghi chat_history cho người khác cùng lúc.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                scope TEXT NOT NULL DEFAULT 'legacy',
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
            """
        )

        # Database cũ chưa có scope. Các dòng cũ sẽ được nhận đúng scope khi
        # người dùng trò chuyện lại trong chính channel đó.
        cursor = await db.execute("PRAGMA table_info(chat_history)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "scope" not in columns:
            await db.execute(
                "ALTER TABLE chat_history "
                "ADD COLUMN scope TEXT NOT NULL DEFAULT 'legacy'"
            )

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_history_user "
            "ON chat_history(channel_id, user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_history_scope "
            "ON chat_history(scope, user_id, channel_id, id)"
        )

        # Giữ bảng cũ để /resetmemory có thể dọn dữ liệu sau khi nâng cấp.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_message_count (
                user_id INTEGER PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Bản tóm tắt trí nhớ dài hạn - 1 dòng/người, ghi đè mỗi lần cập
        # nhật, không phình to theo thời gian như chat_history.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_summary (
                user_id INTEGER PRIMARY KEY,
                summary TEXT NOT NULL
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS scoped_message_count (
                user_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, scope)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS scoped_user_summary (
                user_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                summary TEXT NOT NULL,
                PRIMARY KEY (user_id, scope)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS privacy_mode (
                user_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                anonymous INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, scope)
            )
            """
        )
        await db.commit()


async def add_message(
    channel_id: int,
    user_id: int,
    scope: str,
    role: str,
    content: str,
    max_history: int,
) -> None:
    """
    Thêm 1 tin nhắn vào lịch sử của đúng người đó, rồi tự xoá bớt tin cũ
    -> mỗi người chỉ giữ tối đa `max_history` dòng gần nhất, bảng không
    phình to vô hạn theo thời gian.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chat_history "
            "(channel_id, user_id, scope, role, content) "
            "VALUES (?, ?, ?, ?, ?)",
            (channel_id, user_id, scope, role, content),
        )
        await db.execute(
            """
            DELETE FROM chat_history
            WHERE channel_id = ? AND user_id = ? AND scope = ?
            AND id NOT IN (
                SELECT id FROM chat_history
                WHERE channel_id = ? AND user_id = ? AND scope = ?
                ORDER BY id DESC LIMIT ?
            )
            """,
            (
                channel_id,
                user_id,
                scope,
                channel_id,
                user_id,
                scope,
                max_history,
            ),
        )
        await db.commit()


async def get_history(
    channel_id: int,
    user_id: int,
    scope: str,
    limit: int,
) -> list[dict]:
    """Lấy `limit` tin nhắn gần nhất của đúng người đó, theo đúng thứ tự thời gian."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Channel Discord có ID duy nhất, nên có thể gán an toàn lịch sử cũ
        # của chính channel này vào scope mới khi người dùng quay lại.
        await db.execute(
            """
            UPDATE chat_history SET scope = ?
            WHERE channel_id = ? AND user_id = ? AND scope = ?
            """,
            (scope, channel_id, user_id, LEGACY_SCOPE),
        )
        await db.commit()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, id FROM chat_history
                WHERE channel_id = ? AND user_id = ? AND scope = ?
                ORDER BY id DESC LIMIT ?
            )
            ORDER BY id ASC
            """,
            (channel_id, user_id, scope, limit),
        )
        rows = await cursor.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]


def get_anonymous_history(scope: str, user_id: int, limit: int) -> list[dict]:
    history = _anonymous_history.get((scope, int(user_id)))
    if not history:
        return []
    return list(history)[-limit:]


def add_anonymous_message(
    scope: str,
    user_id: int,
    role: str,
    content: str,
    max_history: int,
) -> None:
    key = (scope, int(user_id))
    history = _anonymous_history.get(key)
    if history is None or history.maxlen != max_history:
        history = deque(history or (), maxlen=max_history)
        _anonymous_history[key] = history
    history.append({"role": role, "content": content})


def clear_anonymous_history(user_id: int, scope: str | None = None) -> None:
    user_id = int(user_id)
    if scope is not None:
        _anonymous_history.pop((scope, user_id), None)
        return
    for key in [key for key in _anonymous_history if key[1] == user_id]:
        _anonymous_history.pop(key, None)


def clear_anonymous_scope(scope: str) -> None:
    for key in [key for key in _anonymous_history if key[0] == scope]:
        _anonymous_history.pop(key, None)


async def clear_user(user_id: int) -> None:
    """Xoá toàn bộ lịch sử của 1 người, ở TẤT CẢ các kênh."""
    clear_anonymous_history(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_summary WHERE user_id = ?", (user_id,))
        await db.execute(
            "DELETE FROM user_message_count WHERE user_id = ?", (user_id,)
        )
        await db.execute(
            "DELETE FROM scoped_user_summary WHERE user_id = ?", (user_id,)
        )
        await db.execute(
            "DELETE FROM scoped_message_count WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def clear_all() -> None:
    """Xoá sạch lịch sử của TẤT CẢ mọi người, mọi kênh, MỌI SERVER.
    Chỉ nên dùng bởi dev (chủ bot), không nên giao cho admin từng server."""
    _anonymous_history.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chat_history")
        await db.execute("DELETE FROM user_summary")
        await db.execute("DELETE FROM user_message_count")
        await db.execute("DELETE FROM scoped_user_summary")
        await db.execute("DELETE FROM scoped_message_count")
        await db.commit()


async def clear_guild(guild_id: int, channel_ids: list[int]) -> None:
    """
    Xoá lịch sử và tóm tắt thuộc đúng một server; không đụng tới DM hoặc
    server khác. ``channel_ids`` chỉ dùng để dọn dữ liệu legacy.
    """
    scope = scope_for_guild(guild_id)
    clear_anonymous_scope(scope)
    async with aiosqlite.connect(DB_PATH) as db:
        # Dữ liệu mới xóa thẳng theo scope, bao gồm cả thread đã lưu.
        await db.execute("DELETE FROM chat_history WHERE scope = ?", (scope,))
        await db.execute("DELETE FROM scoped_user_summary WHERE scope = ?", (scope,))
        await db.execute("DELETE FROM scoped_message_count WHERE scope = ?", (scope,))

        # Dọn thêm lịch sử legacy theo channel cho database nâng cấp từ bản cũ.
        if channel_ids:
            placeholders = ",".join("?" for _ in channel_ids)
            await db.execute(
                f"DELETE FROM chat_history WHERE channel_id IN ({placeholders})",
                channel_ids,
            )
        await db.commit()


async def get_recent_for_scope(
    user_id: int,
    scope: str,
    limit: int,
) -> list[dict]:
    """Lấy hội thoại gần đây trong đúng DM hoặc đúng một server."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, id FROM chat_history
                WHERE user_id = ? AND scope = ?
                ORDER BY id DESC LIMIT ?
            )
            ORDER BY id ASC
            """,
            (user_id, scope, limit),
        )
        rows = await cursor.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]


async def increment_message_count(user_id: int, scope: str) -> int:
    """Tăng bộ đếm riêng trong đúng scope."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO scoped_message_count (user_id, scope, count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, scope) DO UPDATE SET count = count + 1
            """,
            (user_id, scope),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT count FROM scoped_message_count "
            "WHERE user_id = ? AND scope = ?",
            (user_id, scope),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_summary(user_id: int, scope: str) -> str | None:
    """Lấy tóm tắt dài hạn trong đúng DM hoặc đúng server."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT summary FROM scoped_user_summary "
            "WHERE user_id = ? AND scope = ?",
            (user_id, scope),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_summary(user_id: int, scope: str, summary: str) -> None:
    """Ghi đè tóm tắt dài hạn trong đúng scope."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO scoped_user_summary (user_id, scope, summary)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, scope) DO UPDATE SET summary = excluded.summary
            """,
            (user_id, scope, summary),
        )
        await db.commit()


async def is_anonymous_mode(user_id: int, scope: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT anonymous FROM privacy_mode WHERE user_id = ? AND scope = ?",
            (user_id, scope),
        )
        row = await cursor.fetchone()
        return bool(row and row[0])


async def set_anonymous_mode(user_id: int, scope: str, enabled: bool) -> None:
    clear_anonymous_history(user_id, scope)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO privacy_mode (user_id, scope, anonymous)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, scope)
            DO UPDATE SET anonymous = excluded.anonymous
            """,
            (user_id, scope, int(enabled)),
        )
        await db.commit()
