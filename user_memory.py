"""
Lưu trữ lịch sử chat lâu dài theo từng (channel_id, user_id) bằng SQLite.
Thay thế cho deque trong bộ nhớ RAM trước đây - giờ sống sót qua mỗi lần
restart bot. Dùng aiosqlite để không block event loop của discord.py.
"""

import aiosqlite

DB_PATH = "bot_memory.db"


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
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_history_user "
            "ON chat_history(channel_id, user_id)"
        )
        # Index riêng theo user_id (không kèm channel_id) - cần cho
        # get_recent_for_user, vì index composite ở trên chỉ dùng được khi
        # query lọc theo channel_id trước, còn get_recent_for_user chỉ lọc
        # theo user_id nên sẽ bị quét toàn bảng nếu thiếu index này.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_history_by_user "
            "ON chat_history(user_id)"
        )
        # Đếm tổng số tin nhắn (mọi kênh) của mỗi người -> để biết khi nào
        # tới ngưỡng cần tóm tắt lại trí nhớ dài hạn.
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
        await db.commit()


async def add_message(
    channel_id: int, user_id: int, role: str, content: str, max_history: int
) -> None:
    """
    Thêm 1 tin nhắn vào lịch sử của đúng người đó, rồi tự xoá bớt tin cũ
    -> mỗi người chỉ giữ tối đa `max_history` dòng gần nhất, bảng không
    phình to vô hạn theo thời gian.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chat_history (channel_id, user_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            (channel_id, user_id, role, content),
        )
        await db.execute(
            """
            DELETE FROM chat_history
            WHERE channel_id = ? AND user_id = ?
            AND id NOT IN (
                SELECT id FROM chat_history
                WHERE channel_id = ? AND user_id = ?
                ORDER BY id DESC LIMIT ?
            )
            """,
            (channel_id, user_id, channel_id, user_id, max_history),
        )
        await db.commit()


async def get_history(channel_id: int, user_id: int, limit: int) -> list[dict]:
    """Lấy `limit` tin nhắn gần nhất của đúng người đó, theo đúng thứ tự thời gian."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, id FROM chat_history
                WHERE channel_id = ? AND user_id = ?
                ORDER BY id DESC LIMIT ?
            )
            ORDER BY id ASC
            """,
            (channel_id, user_id, limit),
        )
        rows = await cursor.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]


async def clear_user(user_id: int) -> None:
    """Xoá toàn bộ lịch sử của 1 người, ở TẤT CẢ các kênh."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_summary WHERE user_id = ?", (user_id,))
        await db.execute(
            "DELETE FROM user_message_count WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def clear_all() -> None:
    """Xoá sạch lịch sử của TẤT CẢ mọi người, mọi kênh, MỌI SERVER.
    Chỉ nên dùng bởi dev (chủ bot), không nên giao cho admin từng server."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chat_history")
        await db.execute("DELETE FROM user_summary")
        await db.execute("DELETE FROM user_message_count")
        await db.commit()


async def clear_guild(channel_ids: list[int]) -> None:
    """
    Xoá lịch sử chat của mọi người, nhưng CHỈ trong các kênh thuộc 1 server
    cụ thể (không đụng tới server khác) - dùng cho lệnh admin từng server.
    KHÔNG xoá bản tóm tắt dài hạn (user_summary), vì đó là dữ liệu gắn với
    CON NGƯỜI (theo họ qua mọi server), không phải dữ liệu của riêng server
    này để admin 1 server có quyền xoá.
    """
    if not channel_ids:
        return
    placeholders = ",".join("?" for _ in channel_ids)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"DELETE FROM chat_history WHERE channel_id IN ({placeholders})",
            channel_ids,
        )
        await db.commit()


async def get_recent_for_user(user_id: int, limit: int) -> list[dict]:
    """Lấy `limit` tin nhắn gần nhất của 1 người, gộp TẤT CẢ các kênh -
    dùng khi tóm tắt trí nhớ dài hạn (cần cái nhìn tổng quát, không chỉ 1 kênh)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, id FROM chat_history
                WHERE user_id = ?
                ORDER BY id DESC LIMIT ?
            )
            ORDER BY id ASC
            """,
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]


async def increment_message_count(user_id: int) -> int:
    """Tăng bộ đếm tin nhắn của 1 người lên 1, trả về tổng số hiện tại -
    dùng để biết khi nào tới ngưỡng cần tóm tắt lại trí nhớ dài hạn."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_message_count (user_id, count) VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET count = count + 1
            """,
            (user_id,),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT count FROM user_message_count WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_summary(user_id: int) -> str | None:
    """Lấy bản tóm tắt trí nhớ dài hạn hiện có của 1 người (None nếu chưa có)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT summary FROM user_summary WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_summary(user_id: int, summary: str) -> None:
    """Ghi đè bản tóm tắt trí nhớ dài hạn của 1 người."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_summary (user_id, summary) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET summary = excluded.summary
            """,
            (user_id, summary),
        )
        await db.commit()