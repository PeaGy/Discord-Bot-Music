"""Thư viện nhạc cá nhân: favorites, playlists và lịch sử nghe bằng SQLite."""

from __future__ import annotations

import hashlib
from contextlib import closing
from html import unescape
import os
import re
import secrets
import sqlite3
from pathlib import Path
import urllib.parse

import aiosqlite
from music.urls import canonical_youtube_url, spotify_track_id


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
        spotify_id = spotify_track_id(url)
        if spotify_id:
            # Keep the key used by existing canonical Spotify URLs in SQLite.
            return f"spotify:track/{spotify_id.casefold()}"
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


def find_known_track_sync(url: str) -> dict | None:
    """Read only public track metadata for an exact known URL, never user IDs.

    Used by the executor-based /play cache fast path. Missing/older databases
    are fine: this must not initialize or migrate the music library at startup.
    """
    key = track_key({"url": url})
    known = None
    try:
        uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as db:
            db.row_factory = sqlite3.Row
            for table, ordering in (
                ("music_recent", "id DESC"),
                ("music_favorites", "created_at DESC"),
                ("music_playlist_tracks", "id DESC"),
            ):
                try:
                    row = db.execute(
                        f"SELECT {TRACK_COLUMNS} FROM {table} "
                        f"WHERE track_key = ? AND title NOT LIKE 'YouTube (bản đã lưu:%' "
                        f"ORDER BY {ordering} LIMIT 1", (key,),
                    ).fetchone()
                except sqlite3.Error:
                    continue
                if row:
                    candidate = _row_to_track(row)
                    if candidate["source"] == "spotify":
                        # Older rows use the YouTube URL with the Spotify catalog duration.
                        candidate["duration"] = 0
                    if known is None:
                        known = candidate
                    if candidate["duration"] > 0:
                        known["duration"] = candidate["duration"]
                        return known
            canonical_url = canonical_youtube_url(url)
            if canonical_url:
                row = db.execute(
                    "SELECT title, author, url, thumbnail, CASE WHEN lookup_key GLOB 'spotify*' "
                    "THEN 0 ELSE duration END AS video_duration FROM music_search_matches "
                    "WHERE url = ? AND title <> '' "
                    "ORDER BY (video_duration > 0) DESC, (lookup_key GLOB 'keyword:*') DESC, rowid DESC "
                    "LIMIT 1",
                    (canonical_url,),
                ).fetchone()
                if row:
                    if known is not None:
                        known["duration"] = row["video_duration"] or 0
                        return known
                    return {"title": row["title"], "author": row["author"], "url": row["url"],
                            "thumbnail": row["thumbnail"], "duration": row["video_duration"] or 0,
                            "source": "youtube", "search_query": None}
    except (sqlite3.Error, OSError, ValueError):
        pass
    return known


def read_search_match(key: str) -> dict | None:
    """Read a remembered video without opening or rebuilding its audio."""
    try:
        uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT title, author, url, duration, thumbnail FROM music_search_matches "
                             "WHERE lookup_key=?", (key,)).fetchone()
            if row and canonical_youtube_url(row["url"]):
                match = dict(row)
                if key.startswith(("spotify:", "spotify-legacy:")):
                    video = db.execute("SELECT duration FROM music_search_matches WHERE lookup_key=?",
                                       ("video:" + row["url"],)).fetchone()
                    if video:
                        match["playback_duration"] = video["duration"]
                return match
    except (OSError, sqlite3.Error):
        pass
    return None


def cached_search_match(key: str) -> dict | None:
    """A remembered selection is usable only while its completed audio exists."""
    from cache_manager import find_cached_audio_path

    match = read_search_match(key)
    try:
        if match and find_cached_audio_path(match["url"]):
            return match
    except OSError:
        pass
    return None


def remember_search_match(key: str, video_url: str, song: dict) -> None:
    url = canonical_youtube_url(video_url)
    if not url or not key:
        return
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=0.2)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS music_search_matches (
                lookup_key TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT, author TEXT,
                duration INTEGER, thumbnail TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_music_search_matches_url ON music_search_matches(url)")
            durations = [(key, song.get("duration") or 0)]
            if song.get("playback_duration") and canonical_youtube_url(song.get("playback_url")) == url:
                durations.append(("video:" + url, song["playback_duration"]))
            db.executemany("""INSERT OR REPLACE INTO music_search_matches
                              (lookup_key,url,title,author,duration,thumbnail) VALUES (?,?,?,?,?,?)""",
                           [(lookup, url, song.get("title"), song.get("author"), duration,
                             song.get("thumbnail")) for lookup, duration in durations])
    except sqlite3.Error:
        pass  # Optional index: a busy or read-only database must not break playback.


async def _matching_saved_keys(db, track: dict, key: str, rows) -> set[str]:
    """Match current keys and Spotify identities written by older bot versions."""
    keys = {stored_key for stored_key, _, _ in rows if stored_key == key}
    url = str(track.get("url") or "")
    spotify_id = spotify_track_id(url)
    if not spotify_id and track.get("source") != "spotify":
        return keys

    if spotify_id:
        keys.update(
            stored_key for stored_key, saved_url, _ in rows
            if spotify_track_id(saved_url) == spotify_id
        )
    candidates = [
        (stored_key, saved_url) for stored_key, saved_url, source in rows
        if source == "spotify" and stored_key not in keys
        and (canonical_youtube_url(saved_url) if spotify_id else spotify_track_id(saved_url))
    ]
    if not candidates:
        return keys

    # Old playback replaced the Spotify URL with a YouTube URL. Bridge it only
    # when search history uniquely identifies the Spotify track, even on a cache miss.
    originals = ({canonical_youtube_url(saved_url) for _, saved_url in candidates}
                 if spotify_id else {canonical_youtube_url(url)})
    originals.discard(None)
    if not originals:
        return keys
    # Each legacy row retains its original URL, even after its video is replaced.
    # Check both URLs together so conflicting Spotify identities stay separate.
    params = []
    for original in originals:
        params.extend((original, "spotify-legacy:" + original.rsplit("=", 1)[-1]))
    values = ", ".join("(?, ?)" for _ in originals)
    try:
        cursor = await db.execute(
            f"WITH legacy(original, alias_key) AS (VALUES {values}) "
            "SELECT legacy.original, MIN(m.lookup_key) FROM legacy "
            "LEFT JOIN music_search_matches a ON a.lookup_key = legacy.alias_key "
            "JOIN music_search_matches m ON m.url IN (legacy.original, a.url) "
            "WHERE m.lookup_key GLOB 'spotify:*' "
            "GROUP BY legacy.original HAVING COUNT(*) = 1", params,
        )
        matched_ids = {original: lookup.removeprefix("spotify:")
                       for original, lookup in await cursor.fetchall()}
    except sqlite3.OperationalError as error:
        # Older libraries may have no search index yet.
        if "no such table: music_search_matches" not in str(error):
            raise
        return keys

    for stored_key, saved_url in candidates:
        original = canonical_youtube_url(saved_url if spotify_id else url)
        if matched_ids.get(original) == (spotify_id or spotify_track_id(saved_url)):
            keys.add(stored_key)
    return keys


async def _migrate_spotify_html_entities(db) -> None:
    marker = await db.execute(
        "INSERT OR IGNORE INTO music_library_migrations(name) VALUES ('spotify_html_entities_v1')"
    )
    if not marker.rowcount:
        return

    # Stored values are plain text. Only repair complete references, so a URL
    # parameter such as &copy=1 is preserved even when another field is escaped.
    entity = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")

    def decode(value):
        return entity.sub(lambda match: unescape(match[0]), value) if value else value

    await db.create_function("decode_spotify_html", 1, decode)
    has_matches = await (await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='music_search_matches'"
    )).fetchone()
    spotify_matches = "(lookup_key GLOB 'spotify:*' OR lookup_key GLOB 'spotify-legacy:*' OR lookup_key GLOB 'video:*')"
    if has_matches:
        await db.create_function("canonical_video", 1, canonical_youtube_url)
    for table in ("music_recent", "music_favorites", "music_playlist_tracks"):
        scope = "source = 'spotify'"
        if has_matches:
            # A cached YouTube link may have copied its title from the Spotify match.
            scope += (f" OR (source = 'youtube' AND EXISTS (SELECT 1 FROM music_search_matches m "
                      f"WHERE m.url = canonical_video({table}.url) AND m.title = {table}.title "
                      f"AND {spotify_matches}))")
        await db.execute(
            f"UPDATE {table} SET title=decode_spotify_html(title), author=decode_spotify_html(author), "
            "search_query=decode_spotify_html(search_query), thumbnail=decode_spotify_html(thumbnail) "
            "WHERE (title LIKE '%&%;%' OR author LIKE '%&%;%' OR search_query LIKE '%&%;%' "
            f"OR thumbnail LIKE '%&%;%') AND ({scope})"
        )
    if has_matches:
        await db.execute(
            "UPDATE music_search_matches SET title=decode_spotify_html(title), author=decode_spotify_html(author), "
            "thumbnail=decode_spotify_html(thumbnail) WHERE (title LIKE '%&%;%' OR author LIKE '%&%;%' "
            f"OR thumbnail LIKE '%&%;%') AND {spotify_matches}"
        )


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS music_library_migrations (
                name TEXT PRIMARY KEY
            );
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

            CREATE TABLE IF NOT EXISTS music_play_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                requester_id INTEGER,
                track_key TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                url TEXT NOT NULL,
                listened_seconds INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
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
            CREATE INDEX IF NOT EXISTS idx_music_events_guild_time
                ON music_play_events(guild_id, played_at DESC);
            """
        )
        await db.execute("BEGIN IMMEDIATE")
        columns = {
            row[1] for row in await (await db.execute("PRAGMA table_info(music_playlists)")).fetchall()
        }
        if "is_shared" not in columns:
            await db.execute(
                "ALTER TABLE music_playlists ADD COLUMN is_shared INTEGER NOT NULL DEFAULT 0"
            )
        if "share_code" not in columns:
            await db.execute("ALTER TABLE music_playlists ADD COLUMN share_code TEXT")
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_music_playlist_share_code "
            "ON music_playlists(share_code) WHERE share_code IS NOT NULL"
        )
        await _migrate_spotify_html_entities(db)
        await db.commit()


async def toggle_favorite(guild_id: int, user_id: int, track: dict) -> str:
    """Thêm/xóa favorite. Trả về added, removed hoặc limit."""
    key = track_key(track)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            SELECT track_key, url, source FROM music_favorites
            WHERE guild_id = ? AND user_id = ? AND (track_key = ? OR source = 'spotify')
            """,
            (guild_id, user_id, key),
        )
        matches = await _matching_saved_keys(db, track, key, await cursor.fetchall())
        if matches:
            await db.executemany(
                """
                DELETE FROM music_favorites
                WHERE guild_id = ? AND user_id = ? AND track_key = ?
                """,
                [(guild_id, user_id, matched_key) for matched_key in matches],
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
        key = track_key(track)
        cursor = await db.execute(
            "SELECT track_key, url, source FROM music_playlist_tracks "
            "WHERE playlist_id = ? AND (track_key = ? OR source = 'spotify')",
            (playlist_id, key),
        )
        if await _matching_saved_keys(db, track, key, await cursor.fetchall()):
            await db.rollback()
            return "duplicate"
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
                key,
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


async def add_tracks_to_playlist(guild_id: int, owner_id: int, name: str, tracks: list[dict]) -> dict:
    """Thêm nhiều bài, tự bỏ bài trùng và tôn trọng giới hạn playlist."""
    added = duplicates = 0
    for track in tracks:
        result = await add_track_to_playlist(guild_id, owner_id, name, track)
        if result == "added":
            added += 1
        elif result == "duplicate":
            duplicates += 1
        elif result in {"limit", "not_found"}:
            return {"status": result, "added": added, "duplicates": duplicates}
    return {"status": "ok", "added": added, "duplicates": duplicates}


async def share_playlist(guild_id: int, owner_id: int, name: str, shared: bool = True) -> str | None:
    _, normalized = normalize_playlist_name(name)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, share_code FROM music_playlists WHERE guild_id=? AND owner_id=? AND normalized_name=?",
            (guild_id, owner_id, normalized),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        code = row[1] or secrets.token_hex(4).upper()
        await db.execute(
            "UPDATE music_playlists SET is_shared=?, share_code=? WHERE id=?",
            (1 if shared else 0, code, row[0]),
        )
        await db.commit()
        return code


async def clone_shared_playlist(guild_id: int, owner_id: int, code: str, new_name: str) -> dict:
    display, normalized = normalize_playlist_name(new_name)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id FROM music_playlists WHERE share_code=? AND is_shared=1",
            (code.strip().upper(),),
        )
        source = await cursor.fetchone()
        if not source:
            return {"status": "not_found", "added": 0}
        cursor = await db.execute(
            f"SELECT {TRACK_COLUMNS} FROM music_playlist_tracks WHERE playlist_id=? ORDER BY position",
            (source["id"],),
        )
        tracks = [_row_to_track(row) for row in await cursor.fetchall()]
    created = await create_playlist(guild_id, owner_id, display)
    if created != "created":
        return {"status": created, "added": 0}
    result = await add_tracks_to_playlist(guild_id, owner_id, normalized, tracks)
    return {"status": "cloned", "added": result["added"]}


async def record_play_event(guild_id: int, track: dict, listened_seconds: int, *, skipped: bool) -> None:
    requester_id = getattr(track.get("requester"), "id", None)
    duration = int(track.get("duration") or 0)
    listened = max(0, int(listened_seconds))
    completed = int(not skipped and (duration <= 0 or listened >= max(30, duration * 0.8)))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO music_play_events
               (guild_id, requester_id, track_key, title, author, url,
                listened_seconds, completed, skipped)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, requester_id, track_key(track), str(track.get("title") or "Unknown"),
             str(track.get("author") or "Unknown"), str(track.get("url") or ""),
             listened, completed, int(skipped)),
        )
        await db.commit()


async def listening_stats(guild_id: int, days: int = 30, user_id: int | None = None) -> dict:
    where = "guild_id=? AND played_at >= datetime('now', ?) AND listened_seconds >= 30"
    params: list = [guild_id, f"-{max(1, days)} days"]
    if user_id is not None:
        where += " AND requester_id=?"
        params.append(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        total = await (await db.execute(
            f"SELECT COUNT(*) plays, COALESCE(SUM(listened_seconds),0) seconds FROM music_play_events WHERE {where}",
            params,
        )).fetchone()
        tracks = await (await db.execute(
            f"SELECT title, author, COUNT(*) plays, SUM(listened_seconds) seconds FROM music_play_events WHERE {where} GROUP BY track_key ORDER BY plays DESC, seconds DESC LIMIT 5",
            params,
        )).fetchall()
        artists = await (await db.execute(
            f"SELECT author, COUNT(*) plays FROM music_play_events WHERE {where} GROUP BY lower(author) ORDER BY plays DESC LIMIT 5",
            params,
        )).fetchall()
        requesters = await (await db.execute(
            f"SELECT requester_id, COUNT(*) plays, SUM(listened_seconds) seconds FROM music_play_events WHERE {where} AND requester_id IS NOT NULL GROUP BY requester_id ORDER BY seconds DESC LIMIT 5",
            params,
        )).fetchall()
    return {"plays": total["plays"], "seconds": total["seconds"],
            "tracks": [dict(x) for x in tracks], "artists": [dict(x) for x in artists],
            "requesters": [dict(x) for x in requesters]}
