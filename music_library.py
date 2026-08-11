"""Thư viện nhạc cá nhân: favorites, playlists và lịch sử nghe bằng SQLite."""

from __future__ import annotations

import hashlib
import os
import re
import urllib.parse

import aiosqlite


DB_PATH = os.getenv("MUSIC_LIBRARY_DB", "music_library.db")
MAX_FAVORITES = 100
MAX_PLAYLISTS = 25
MAX_PLAYLIST_TRACKS = 100
MAX_RECENT_PER_GUILD = 100

TRACK_COLUMNS = "title, author, url, search_query, duration, thumbnail, source"


def normalize_playlist_name(name: str) -> tuple[str, str]:
    """Trả về tên hiển thị và khóa so sánh; từ chối tên rỗng/quá dài."""
    display_name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not display_name:
        raise ValueError("Tên playlist không được để trống.")
    if len(display_name) > 50:
        raise ValueError("Tên playlist chỉ được dài tối đa 50 ký tự.")
    return display_name, display_name.casefold()


def _canonical_track_identity(track: dict) -> str:
    url = str(track.get("url") or "").strip()
    if url:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.casefold().removeprefix("www.")
        if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
            video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
            if video_id:
                return f"youtube:{video_id}"
        if host == "youtu.be":
            video_id = parsed.path.strip("/").split("/", 1)[0]
            if video_id:
                return f"youtube:{video_id}"
        if host == "open.spotify.com":
            return f"spotify:{parsed.path.strip('/').casefold()}"
        return url.casefold()

    search_query = str(track.get("search_query") or "").strip()
    if search_query:
        return f"search:{search_query.casefold()}"

    title = str(track.get("title") or "Unknown").strip().casefold()
    author = str(track.get("author") or "Unknown").strip().casefold()
    return f"metadata:{title}|{author}"


def track_key(track: dict) -> str:
    identity = _canonical_track_identity(track)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _track_values(track: dict) -> tuple:
    try:
        duration = int(track.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0

    return (
        str(track.get("title") or "Unknown"),
        str(track.get("author") or "Unknown"),
        str(track.get("url") or ""),
        str(track.get("search_query") or ""),
        duration,
        str(track.get("thumbnail") or ""),
        str(track.get("source") or "youtube"),
    )


def _row_to_track(row: aiosqlite.Row) -> dict:
    return {
        "title": row["title"],
        "author": row["author"],
        "url": row["url"],
        "search_query": row["search_query"] or None,
        "duration": row["duration"],
        "thumbnail": row["thumbnail"] or None,
        "source": row["source"],
    }


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS music_favorites (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                track_key TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                url TEXT NOT NULL,
                search_query TEXT NOT NULL DEFAULT '',
                duration INTEGER NOT NULL DEFAULT 0,
                thumbnail TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'youtube',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id, track_key)
            );

            CREATE TABLE IF NOT EXISTS music_playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (guild_id, owner_id, normalized_name)
            );

            CREATE TABLE IF NOT EXISTS music_playlist_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                track_key TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                url TEXT NOT NULL,
                search_query TEXT NOT NULL DEFAULT '',
                duration INTEGER NOT NULL DEFAULT 0,
                thumbnail TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'youtube',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (playlist_id) REFERENCES music_playlists(id)
                    ON DELETE CASCADE,
                UNIQUE (playlist_id, position)
            );

            CREATE TABLE IF NOT EXISTS music_recent (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                requester_id INTEGER,
                track_key TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                url TEXT NOT NULL,
                search_query TEXT NOT NULL DEFAULT '',
                duration INTEGER NOT NULL DEFAULT 0,
                thumbnail TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'youtube',
                played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_music_favorites_user
                ON music_favorites(guild_id, user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_music_playlists_owner
                ON music_playlists(guild_id, owner_id, normalized_name);
            CREATE INDEX IF NOT EXISTS idx_music_playlist_tracks_order
                ON music_playlist_tracks(playlist_id, position);
            CREATE INDEX IF NOT EXISTS idx_music_recent_guild
                ON music_recent(guild_id, id DESC);
            """
        )
        await db.commit()


async def toggle_favorite(guild_id: int, user_id: int, track: dict) -> str:
    """Thêm/xóa favorite. Trả về added, removed hoặc limit."""
    key = track_key(track)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            SELECT 1 FROM music_favorites
            WHERE guild_id = ? AND user_id = ? AND track_key = ?
            """,
            (guild_id, user_id, key),
        )
        if await cursor.fetchone():
            await db.execute(
                """
                DELETE FROM music_favorites
                WHERE guild_id = ? AND user_id = ? AND track_key = ?
                """,
                (guild_id, user_id, key),
            )
            await db.commit()
            return "removed"

        cursor = await db.execute(
            "SELECT COUNT(*) FROM music_favorites WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        count = (await cursor.fetchone())[0]
        if count >= MAX_FAVORITES:
            await db.rollback()
            return "limit"

        await db.execute(
            f"""
            INSERT INTO music_favorites (
                guild_id, user_id, track_key, {TRACK_COLUMNS}
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, key, *_track_values(track)),
        )
        await db.commit()
        return "added"


async def list_favorites(guild_id: int, user_id: int, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT {TRACK_COLUMNS} FROM music_favorites
            WHERE guild_id = ? AND user_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (guild_id, user_id, limit),
        )
        return [_row_to_track(row) for row in await cursor.fetchall()]


async def create_playlist(guild_id: int, owner_id: int, name: str) -> str:
    display_name, normalized_name = normalize_playlist_name(name)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT COUNT(*) FROM music_playlists WHERE guild_id = ? AND owner_id = ?",
            (guild_id, owner_id),
        )
        if (await cursor.fetchone())[0] >= MAX_PLAYLISTS:
            await db.rollback()
            return "limit"
        try:
            await db.execute(
                """
                INSERT INTO music_playlists
                    (guild_id, owner_id, name, normalized_name)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, owner_id, display_name, normalized_name),
            )
        except aiosqlite.IntegrityError:
            await db.rollback()
            return "exists"
        await db.commit()
        return "created"


async def list_playlists(guild_id: int, owner_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT p.name, COUNT(t.id) AS track_count
            FROM music_playlists AS p
            LEFT JOIN music_playlist_tracks AS t ON t.playlist_id = p.id
            WHERE p.guild_id = ? AND p.owner_id = ?
            GROUP BY p.id
            ORDER BY p.normalized_name
            """,
            (guild_id, owner_id),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def add_track_to_playlist(
    guild_id: int,
    owner_id: int,
    name: str,
    track: dict,
) -> str:
    _, normalized_name = normalize_playlist_name(name)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            SELECT id FROM music_playlists
            WHERE guild_id = ? AND owner_id = ? AND normalized_name = ?
            """,
            (guild_id, owner_id, normalized_name),
        )
        playlist = await cursor.fetchone()
        if not playlist:
            await db.rollback()
            return "not_found"

        playlist_id = playlist[0]
        cursor = await db.execute(
            """
            SELECT COUNT(*), COALESCE(MAX(position), 0)
            FROM music_playlist_tracks WHERE playlist_id = ?
            """,
            (playlist_id,),
        )
        count, last_position = await cursor.fetchone()
        if count >= MAX_PLAYLIST_TRACKS:
            await db.rollback()
            return "limit"

        await db.execute(
            f"""
            INSERT INTO music_playlist_tracks (
                playlist_id, position, track_key, {TRACK_COLUMNS}
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playlist_id,
                last_position + 1,
                track_key(track),
                *_track_values(track),
            ),
        )
        await db.commit()
        return "added"


async def get_playlist_tracks(guild_id: int, owner_id: int, name: str) -> list[dict] | None:
    _, normalized_name = normalize_playlist_name(name)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id FROM music_playlists
            WHERE guild_id = ? AND owner_id = ? AND normalized_name = ?
            """,
            (guild_id, owner_id, normalized_name),
        )
        playlist = await cursor.fetchone()
        if not playlist:
            return None

        cursor = await db.execute(
            f"""
            SELECT {TRACK_COLUMNS} FROM music_playlist_tracks
            WHERE playlist_id = ? ORDER BY position
            """,
            (playlist["id"],),
        )
        return [_row_to_track(row) for row in await cursor.fetchall()]


async def delete_playlist(guild_id: int, owner_id: int, name: str) -> bool:
    _, normalized_name = normalize_playlist_name(name)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        cursor = await db.execute(
            """
            DELETE FROM music_playlists
            WHERE guild_id = ? AND owner_id = ? AND normalized_name = ?
            """,
            (guild_id, owner_id, normalized_name),
        )
        await db.commit()
        return cursor.rowcount > 0


async def record_recent(guild_id: int, track: dict) -> None:
    requester = track.get("requester")
    requester_id = getattr(requester, "id", None)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            INSERT INTO music_recent (
                guild_id, requester_id, track_key, {TRACK_COLUMNS}
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, requester_id, track_key(track), *_track_values(track)),
        )
        await db.execute(
            """
            DELETE FROM music_recent
            WHERE guild_id = ? AND id NOT IN (
                SELECT id FROM music_recent
                WHERE guild_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (guild_id, guild_id, MAX_RECENT_PER_GUILD),
        )
        await db.commit()


async def list_recent(guild_id: int, limit: int = 15) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT {TRACK_COLUMNS} FROM music_recent
            WHERE guild_id = ? ORDER BY id DESC LIMIT ?
            """,
            (guild_id, limit),
        )
        return [_row_to_track(row) for row in await cursor.fetchall()]
