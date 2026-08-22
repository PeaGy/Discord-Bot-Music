"""Lịch sử AI theo kênh; trí nhớ dài hạn cá nhân dùng chung mọi server/DM."""

from collections import deque
import re

import aiosqlite

DB_PATH = "bot_memory.db"
LEGACY_SCOPE = "legacy"
DM_SCOPE = "dm"
GLOBAL_MEMORY_SCOPE = "user:global"
AI_BLACKLIST_DENIAL_MESSAGE = "Bạn không có quyền để chat với tôi."

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
            "CREATE INDEX IF NOT EXISTS idx_chat_history_user_id "
            "ON chat_history(user_id, id)"
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
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS explicit_user_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, content)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_explicit_memory_user "
            "ON explicit_user_memory(user_id, id)"
        )
        # Ghi rõ mỗi Discord message do Peto gửi được tạo để trả lời ai.
        # Đây là metadata hội thoại, không phải trí nhớ cá nhân; nó giúp một
        # người thứ ba reply câu của Peto mà model không nhận nhầm phong cách
        # của người đã hỏi ban đầu là do người hiện tại "dạy".
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_response_provenance (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER,
                requester_user_id INTEGER NOT NULL,
                requester_display_name TEXT NOT NULL DEFAULT '',
                source_message_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_provenance_channel "
            "ON bot_response_provenance(channel_id, message_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_provenance_requester "
            "ON bot_response_provenance(requester_user_id, message_id)"
        )
        # Danh sách chặn trò chuyện AI là dữ liệu quản trị, độc lập với trí nhớ.
        # Vì vậy /resetmemory và /resetmemoryglobal không được xóa bảng này.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_chat_blacklist (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Mỗi lần tóm tắt đều giữ lại một phiên bản bất biến. Bản mới nhất vẫn
        # nằm trong scoped_user_summary để đọc nhanh, còn các bản cũ giúp phục
        # hồi một chi tiết từng bị model tóm tắt sau đó lược bỏ.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_summary_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, summary)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_summary_versions_user "
            "ON memory_summary_versions(user_id, id)"
        )

        # Nâng cấp từ cơ chế tóm tắt tách theo DM/server sang trí nhớ cá nhân
        # dùng chung. Chỉ gom một lần rồi xóa các bản scope cũ để lần khởi động
        # sau không nhập trùng nội dung.
        cursor = await db.execute(
            """
            SELECT user_id, summary FROM scoped_user_summary
            WHERE scope != ? ORDER BY user_id, scope
            """,
            (GLOBAL_MEMORY_SCOPE,),
        )
        legacy_summaries: dict[int, list[str]] = {}
        for user_id, summary in await cursor.fetchall():
            clean = str(summary or "").strip()
            if clean:
                legacy_summaries.setdefault(int(user_id), []).append(clean)
        for user_id, summaries in legacy_summaries.items():
            existing_cursor = await db.execute(
                "SELECT summary FROM scoped_user_summary "
                "WHERE user_id = ? AND scope = ?",
                (user_id, GLOBAL_MEMORY_SCOPE),
            )
            existing_row = await existing_cursor.fetchone()
            combined = "\n".join(
                dict.fromkeys(
                    ([str(existing_row[0]).strip()] if existing_row and existing_row[0] else [])
                    + summaries
                )
            )[:6000]
            await db.execute(
                """
                INSERT INTO scoped_user_summary (user_id, scope, summary)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, scope) DO UPDATE SET summary = excluded.summary
                """,
                (user_id, GLOBAL_MEMORY_SCOPE, combined),
            )
        await db.execute(
            "DELETE FROM scoped_user_summary WHERE scope != ?",
            (GLOBAL_MEMORY_SCOPE,),
        )

        # Bộ đếm tóm tắt cũng theo người dùng toàn cục để hội thoại ở server
        # khác tiếp tục chu kỳ cập nhật thay vì bắt đầu lại từ đầu.
        cursor = await db.execute(
            """
            SELECT user_id, COALESCE(SUM(count), 0)
            FROM scoped_message_count WHERE scope != ? GROUP BY user_id
            """,
            (GLOBAL_MEMORY_SCOPE,),
        )
        for user_id, count in await cursor.fetchall():
            await db.execute(
                """
                INSERT INTO scoped_message_count (user_id, scope, count)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, scope) DO UPDATE
                SET count = MAX(scoped_message_count.count, excluded.count)
                """,
                (int(user_id), GLOBAL_MEMORY_SCOPE, int(count)),
            )
        await db.execute(
            "DELETE FROM scoped_message_count WHERE scope != ?",
            (GLOBAL_MEMORY_SCOPE,),
        )
        # Giữ lại bản tóm tắt đang có trước khi cơ chế versioning được cài.
        await db.execute(
            """
            INSERT OR IGNORE INTO memory_summary_versions (user_id, summary)
            SELECT user_id, summary FROM scoped_user_summary
            WHERE scope = ? AND TRIM(summary) != ''
            """,
            (GLOBAL_MEMORY_SCOPE,),
        )
        await db.commit()


async def is_ai_blacklisted(user_id: int) -> bool:
    """Kiểm tra một Discord user ID có bị cấm trò chuyện với Peto hay không."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM ai_chat_blacklist WHERE user_id = ? LIMIT 1",
            (int(user_id),),
        )
        return await cursor.fetchone() is not None


async def add_ai_blacklist(user_id: int, added_by: int) -> bool:
    """Thêm user vào blacklist; trả True nếu đây là mục mới."""
    async with aiosqlite.connect(DB_PATH) as db:
        before = db.total_changes
        await db.execute(
            "INSERT OR IGNORE INTO ai_chat_blacklist (user_id, added_by) "
            "VALUES (?, ?)",
            (int(user_id), int(added_by)),
        )
        await db.commit()
        return db.total_changes > before


async def remove_ai_blacklist(user_id: int) -> bool:
    """Bỏ user khỏi blacklist; trả True nếu đã xóa một mục."""
    async with aiosqlite.connect(DB_PATH) as db:
        before = db.total_changes
        await db.execute(
            "DELETE FROM ai_chat_blacklist WHERE user_id = ?",
            (int(user_id),),
        )
        await db.commit()
        return db.total_changes > before


async def add_message(
    channel_id: int,
    user_id: int,
    scope: str,
    role: str,
    content: str,
    max_history: int | None,
) -> None:
    """
    Thêm một tin nhắn vào lịch sử của đúng người đó. ``max_history=None`` giữ
    nguyên kho lịch sử; số dương chỉ dành cho cấu hình muốn cắt dữ liệu cũ.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chat_history "
            "(channel_id, user_id, scope, role, content) "
            "VALUES (?, ?, ?, ?, ?)",
            (channel_id, user_id, scope, role, content),
        )
        if max_history is not None and int(max_history) > 0:
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
                    int(max_history),
                ),
            )
        await db.commit()


async def record_bot_response_provenance(
    message_id: int,
    channel_id: int,
    guild_id: int | None,
    requester_user_id: int,
    requester_display_name: str,
    source_message_id: int | None,
) -> None:
    """Lưu người đã kích hoạt một câu trả lời Discord cụ thể của Peto."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO bot_response_provenance (
                message_id, channel_id, guild_id, requester_user_id,
                requester_display_name, source_message_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                guild_id = excluded.guild_id,
                requester_user_id = excluded.requester_user_id,
                requester_display_name = excluded.requester_display_name,
                source_message_id = excluded.source_message_id
            """,
            (
                int(message_id),
                int(channel_id),
                int(guild_id) if guild_id is not None else None,
                int(requester_user_id),
                str(requester_display_name or "")[:200],
                int(source_message_id) if source_message_id is not None else None,
            ),
        )
        await db.commit()


async def get_bot_response_provenance(
    message_ids: list[int],
) -> dict[int, dict]:
    """Trả metadata nguồn gốc cho một nhóm message của Peto."""
    ids = list(dict.fromkeys(int(value) for value in message_ids if value))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT message_id, requester_user_id, requester_display_name,
                   source_message_id
            FROM bot_response_provenance
            WHERE message_id IN ({placeholders})
            """,
            ids,
        )
        rows = await cursor.fetchall()
    return {
        int(row["message_id"]): {
            "requester_user_id": int(row["requester_user_id"]),
            "requester_display_name": str(row["requester_display_name"] or ""),
            "source_message_id": (
                int(row["source_message_id"])
                if row["source_message_id"] is not None
                else None
            ),
        }
        for row in rows
    }


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
        await db.execute(
            "DELETE FROM explicit_user_memory WHERE user_id = ?", (user_id,)
        )
        await db.execute(
            "DELETE FROM memory_summary_versions WHERE user_id = ?", (user_id,)
        )
        await db.execute(
            "DELETE FROM bot_response_provenance WHERE requester_user_id = ?",
            (user_id,),
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
        await db.execute("DELETE FROM explicit_user_memory")
        await db.execute("DELETE FROM memory_summary_versions")
        await db.execute("DELETE FROM bot_response_provenance")
        await db.commit()


async def clear_guild(guild_id: int, channel_ids: list[int]) -> None:
    """
    Xoá lịch sử thuộc đúng một server. Trí nhớ dài hạn cá nhân dùng chung nên
    admin server không được phép xóa nó. ``channel_ids`` dùng dọn legacy.
    """
    scope = scope_for_guild(guild_id)
    clear_anonymous_scope(scope)
    async with aiosqlite.connect(DB_PATH) as db:
        # Dữ liệu mới xóa thẳng theo scope, bao gồm cả thread đã lưu.
        await db.execute("DELETE FROM chat_history WHERE scope = ?", (scope,))
        # Dọn thêm lịch sử legacy theo channel cho database nâng cấp từ bản cũ.
        if channel_ids:
            placeholders = ",".join("?" for _ in channel_ids)
            await db.execute(
                f"DELETE FROM chat_history WHERE channel_id IN ({placeholders})",
                channel_ids,
            )
        await db.execute(
            "DELETE FROM bot_response_provenance WHERE guild_id = ?",
            (int(guild_id),),
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


async def get_recent_for_user(
    user_id: int,
    limit: int,
) -> list[dict]:
    """Lấy hội thoại gần nhất của một Discord user trên mọi server và DM.

    Chỉ dùng dữ liệu đã lưu bền vững; nội dung Ẩn danh nằm trong RAM nên không
    thể lọt vào bản tóm tắt cá nhân dùng chung.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT role, content, scope FROM (
                SELECT role, content, scope, id FROM chat_history
                WHERE user_id = ?
                ORDER BY id DESC LIMIT ?
            )
            ORDER BY id ASC
            """,
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "scope": row["scope"],
            }
            for row in rows
        ]


_MEMORY_SEARCH_STOPWORDS = {
    "peto", "pearto", "bot", "bạn", "ban", "mình", "minh", "tôi", "toi",
    "ta", "chúng", "chung", "tụi", "tui", "có", "co", "không", "khong",
    "còn", "con", "nhớ", "nho", "đã", "da", "từng", "tung", "về", "ve",
    "gì", "gi", "là", "la", "mà", "ma", "của", "cua", "ở", "o", "lúc",
    "luc", "trước", "truoc", "đây", "day", "lần", "lan", "nào", "nao",
    "what", "do", "you", "remember", "about", "we", "did", "before",
}


def _memory_search_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[^\W_]+", str(query or "").casefold(), re.UNICODE):
        if len(token) < 2 or token in _MEMORY_SEARCH_STOPWORDS or token.isdigit():
            continue
        if token not in terms:
            terms.append(token)
    return terms[:10]


async def search_user_history(
    user_id: int,
    query: str,
    limit: int = 14,
) -> list[dict]:
    """Tìm cục bộ những đoạn lịch sử liên quan của đúng user_id.

    Đây chỉ chạy khi người dùng chủ động hỏi lại ký ức. Không có request AI hay
    dịch vụ embedding riêng, nên độ trễ chủ yếu là một truy vấn SQLite nhỏ.
    """
    terms = _memory_search_terms(query)
    query_tokens = re.findall(
        r"[^\W_]+", str(query or "").casefold(), re.UNICODE
    )
    phrases = []
    for size in (3, 2):
        for index in range(len(query_tokens) - size + 1):
            phrase = " ".join(query_tokens[index:index + size])
            meaningful = [
                token for token in query_tokens[index:index + size]
                if token not in _MEMORY_SEARCH_STOPWORDS
            ]
            if len(meaningful) >= 2 and phrase not in phrases:
                phrases.append(phrase)
    phrases = phrases[:8]

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if terms:
            clauses = " OR ".join("LOWER(content) LIKE ?" for _ in terms)
            params = [int(user_id), *[f"%{term}%" for term in terms], 160]
            cursor = await db.execute(
                f"""
                SELECT id, role, content, scope FROM chat_history
                WHERE user_id = ? AND ({clauses})
                ORDER BY id DESC LIMIT ?
                """,
                params,
            )
            explicit_clauses = " OR ".join(
                "LOWER(content) LIKE ?" for _ in terms
            )
            explicit_cursor = await db.execute(
                f"""
                SELECT id, content FROM explicit_user_memory
                WHERE user_id = ? AND ({explicit_clauses})
                ORDER BY id DESC LIMIT 80
                """,
                [int(user_id), *[f"%{term}%" for term in terms]],
            )
            summary_clauses = " OR ".join(
                "LOWER(summary) LIKE ?" for _ in terms
            )
            summary_cursor = await db.execute(
                f"""
                SELECT id, summary FROM memory_summary_versions
                WHERE user_id = ? AND ({summary_clauses})
                ORDER BY id DESC LIMIT 80
                """,
                [int(user_id), *[f"%{term}%" for term in terms]],
            )
        else:
            cursor = await db.execute(
                """
                SELECT id, role, content, scope FROM chat_history
                WHERE user_id = ? ORDER BY id DESC LIMIT ?
                """,
                (int(user_id), max(int(limit) * 3, 30)),
            )
            explicit_cursor = await db.execute(
                """
                SELECT id, content FROM explicit_user_memory
                WHERE user_id = ? ORDER BY id DESC LIMIT 20
                """,
                (int(user_id),),
            )
            summary_cursor = await db.execute(
                """
                SELECT id, summary FROM memory_summary_versions
                WHERE user_id = ? ORDER BY id DESC LIMIT 20
                """,
                (int(user_id),),
            )
        rows = await cursor.fetchall()
        explicit_rows = await explicit_cursor.fetchall()
        summary_rows = await summary_cursor.fetchall()

    negative_memory_phrases = (
        "không nhớ", "khong nho", "không còn nhớ", "khong con nho",
        "không nắm được", "khong nam duoc", "quên mất", "quen mat",
        "không có trong trí nhớ", "khong co trong tri nho",
        "i don't remember", "i do not remember",
    )

    def score_item(content: str, *, source: str, role: str, item_id: int) -> tuple:
        folded = content.casefold()
        score = sum(folded.count(term) * 4 for term in terms)
        score += sum(18 for phrase in phrases if phrase in folded)
        if source == "explicit":
            score += 100
        elif source == "summary_version":
            score += 55
        if role == "assistant" and any(
            phrase in folded for phrase in negative_memory_phrases
        ):
            score -= 120
        return score, item_id

    scored: list[tuple[int, int, dict]] = []
    for row in rows:
        content = str(row["content"] or "")
        item_id = int(row["id"])
        item = {
            "id": item_id,
            "role": row["role"],
            "content": content,
            "scope": row["scope"],
            "source": "chat_history",
        }
        score, recency = score_item(
            content, source="chat_history", role=str(row["role"]), item_id=item_id
        )
        scored.append((score, recency, item))

    for row in summary_rows:
        content = str(row["summary"] or "")
        item_id = int(row["id"])
        item = {
            "id": item_id,
            "role": "memory_summary",
            "content": content,
            "scope": GLOBAL_MEMORY_SCOPE,
            "source": "summary_version",
        }
        score, recency = score_item(
            content, source="summary_version", role="memory_summary", item_id=item_id
        )
        scored.append((score, recency, item))

    for row in explicit_rows:
        content = str(row["content"] or "")
        item_id = int(row["id"])
        item = {
            "id": item_id,
            "role": "pinned_memory",
            "content": content,
            "scope": GLOBAL_MEMORY_SCOPE,
            "source": "explicit",
        }
        score, recency = score_item(
            content, source="explicit", role="pinned_memory", item_id=item_id
        )
        scored.append((score, recency, item))

    # Chọn theo độ liên quan + độ mới, sau đó trả theo thời gian để model hiểu
    # diễn biến và ưu tiên bản sửa/chốt mới nhất.
    chosen = sorted(scored, key=lambda entry: (entry[0], entry[1]), reverse=True)[
        : max(1, int(limit))
    ]
    # Giữ thứ tự ưu tiên để dữ kiện đã ghim đứng trước lời phủ nhận gần đây.
    return [entry[2] for entry in chosen]


async def add_explicit_memory(
    user_id: int,
    content: str,
) -> None:
    """Lưu nguyên ý một điều người dùng chủ động yêu cầu nhớ, theo user_id."""
    clean = str(content or "").strip()[:6000]
    if not clean:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO explicit_user_memory (user_id, content) "
            "VALUES (?, ?)",
            (int(user_id), clean),
        )
        await db.commit()


async def get_explicit_memories(user_id: int, limit: int = 12) -> list[str]:
    """Lấy các điều đã chốt của đúng user, cũ → mới để mục mới được ưu tiên."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT content FROM (
                SELECT content, id FROM explicit_user_memory
                WHERE user_id = ? ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (int(user_id), int(limit)),
        )
        return [str(row[0]) for row in await cursor.fetchall() if row[0]]


async def get_relevant_explicit_memories(
    user_id: int,
    query: str,
    *,
    limit: int = 4,
    recent_fallback: int = 2,
) -> list[str]:
    """Chọn ký ức ghim liên quan, kèm vài mục mới nhất để giữ tính liên tục.

    Toàn bộ dữ liệu vẫn nằm trong SQLite; hàm chỉ giảm phần phải gửi lên LLM ở
    một lượt chat bình thường. Câu hỏi nhớ lại vẫn dùng ``search_user_history``.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT id, content FROM explicit_user_memory
            WHERE user_id = ? ORDER BY id DESC LIMIT 100
            """,
            (int(user_id),),
        )
        rows = await cursor.fetchall()
    if not rows:
        return []

    terms = _memory_search_terms(query)
    scored: list[tuple[int, int, str]] = []
    for row_id, content in rows:
        text = str(content or "")
        folded = text.casefold()
        score = sum(folded.count(term) for term in terms)
        if score:
            scored.append((score, int(row_id), text))

    selected: dict[int, str] = {}
    for _, row_id, content in sorted(
        scored, key=lambda item: (item[0], item[1]), reverse=True
    )[:max(0, int(limit))]:
        selected[row_id] = content
    for row_id, content in rows[:max(0, int(recent_fallback))]:
        selected.setdefault(int(row_id), str(content))

    # Cũ → mới để lời chốt/sửa gần nhất đứng sau và được model ưu tiên.
    chosen = sorted(selected.items(), key=lambda item: item[0])
    return [content for _, content in chosen[-max(1, int(limit)):]]


async def prune_invalid_explicit_memories(predicate) -> int:
    """Dọn các câu hỏi nhớ lại từng bị bản cũ nhận nhầm thành dữ kiện mới."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, content FROM explicit_user_memory")
        invalid_ids = [
            (int(row_id),)
            for row_id, content in await cursor.fetchall()
            if content and not predicate(str(content))
        ]
        if not invalid_ids:
            return 0
        before = db.total_changes
        await db.executemany(
            "DELETE FROM explicit_user_memory WHERE id = ?", invalid_ids
        )
        await db.commit()
        return db.total_changes - before


async def backfill_explicit_memories(predicate, scan_limit: int = 5000) -> int:
    """Nhập các câu chốt gần đây từ database cũ; INSERT OR IGNORE nên chạy lại an toàn."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT user_id, content FROM chat_history
            WHERE role = 'user' ORDER BY id DESC LIMIT ?
            """,
            (int(scan_limit),),
        )
        matches = [
            (int(user_id), str(content))
            for user_id, content in await cursor.fetchall()
            if content and predicate(str(content))
        ]
        # Query đang mới → cũ; chèn đảo lại để id tăng theo đúng thời gian.
        matches.reverse()
        before = db.total_changes
        await db.executemany(
            "INSERT OR IGNORE INTO explicit_user_memory (user_id, content) "
            "VALUES (?, ?)",
            matches,
        )
        await db.commit()
        return db.total_changes - before


async def increment_message_count(user_id: int, scope: str) -> int:
    """Tăng bộ đếm tóm tắt dùng chung của người dùng (scope giữ để tương thích)."""
    scope = GLOBAL_MEMORY_SCOPE
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
    """Lấy trí nhớ dài hạn dùng chung của người dùng ở mọi server và DM."""
    scope = GLOBAL_MEMORY_SCOPE
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT summary FROM scoped_user_summary "
            "WHERE user_id = ? AND scope = ?",
            (user_id, scope),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_summary(user_id: int, scope: str, summary: str) -> None:
    """Cập nhật bản đọc nhanh và giữ một phiên bản bất biến để phục hồi."""
    scope = GLOBAL_MEMORY_SCOPE
    summary = str(summary or "").strip()[:12000]
    if not summary:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO scoped_user_summary (user_id, scope, summary)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, scope) DO UPDATE SET summary = excluded.summary
            """,
            (user_id, scope, summary),
        )
        await db.execute(
            "INSERT OR IGNORE INTO memory_summary_versions (user_id, summary) "
            "VALUES (?, ?)",
            (int(user_id), summary),
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
