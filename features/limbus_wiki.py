from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import sqlite3
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("%s không hợp lệ; dùng mặc định %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("%s không hợp lệ; dùng mặc định %s", name, default)
        return default


API_URL = "https://limbuscompany.wiki.gg/api.php"
WIKI_ROOT = "https://limbuscompany.wiki.gg/wiki/"
DB_PATH = Path(os.getenv("LIMBUS_WIKI_DB", "limbus_knowledge.db")).resolve()
SYNC_HOURS = max(1.0, _env_float("LIMBUS_WIKI_SYNC_HOURS", 12.0))
SYNC_CONCURRENCY = max(1, min(3, _env_int("LIMBUS_WIKI_SYNC_CONCURRENCY", 2)))
REQUEST_DELAY = max(0.05, _env_float("LIMBUS_WIKI_REQUEST_DELAY", 0.20))
ASSET_THUMB_SIZE = max(256, min(1200, _env_int("LIMBUS_ASSET_THUMB_SIZE", 700)))
INDEX_VERSION = "2"
CHUNK_CHARS = 2400
CHUNK_OVERLAP = 250
MAX_PAGE_TEXT = 180_000
USER_AGENT = os.getenv(
    "LIMBUS_WIKI_USER_AGENT",
    "PetoDiscordBot/1.0 (personal Discord knowledge assistant)",
).strip()

# These pages transclude large roster templates. Their rendered contents may
# change even when the wrapper page revision stays the same, so revision-only
# sync would leave gacha/exchange catalogs stale.
ALWAYS_REFRESH_RENDERED_TITLES = frozenset(
    {
        "Extraction/Extraction List",
        "List of Identities/Rarity",
        "List of E.G.O",
        "List of E.G.O/Data",
    }
)


def _catalog_page_needs_refresh(
    title: str, known_revid: int | None, live_revid: int
) -> bool:
    return known_revid != live_revid or title in ALWAYS_REFRESH_RENDERED_TITLES

OFFICIAL_NEWS_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS official_news_image_cache (
    image_hash TEXT PRIMARY KEY,
    image_url TEXT NOT NULL DEFAULT '',
    notice_url TEXT NOT NULL DEFAULT '',
    extracted_text TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_official_news_image_url
    ON official_news_image_cache(image_url);
CREATE TABLE IF NOT EXISTS official_news_answer_cache (
    query_key TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL
);
"""

ASSET_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_assets (
    pageid INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    revid INTEGER NOT NULL DEFAULT 0,
    file_title TEXT NOT NULL DEFAULT '',
    original_url TEXT NOT NULL DEFAULT '',
    thumbnail_url TEXT NOT NULL DEFAULT '',
    synced_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wiki_assets_title
    ON wiki_assets(title COLLATE NOCASE);
"""


def _news_cache_connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _init_official_news_cache_sync() -> None:
    with closing(_news_cache_connect()) as db:
        db.executescript(OFFICIAL_NEWS_CACHE_SCHEMA)


async def init_official_news_cache() -> None:
    await asyncio.to_thread(_init_official_news_cache_sync)


def _get_news_image_cache_sync(image_hash: str) -> dict | None:
    with closing(_news_cache_connect()) as db:
        row = db.execute(
            "SELECT extracted_text, image_url, notice_url, created_at "
            "FROM official_news_image_cache WHERE image_hash = ?",
            (str(image_hash),),
        ).fetchone()
        if not row:
            return None
        db.execute(
            "UPDATE official_news_image_cache SET last_used_at = ? "
            "WHERE image_hash = ?",
            (int(time.time()), str(image_hash)),
        )
        db.commit()
        return dict(row)


async def get_news_image_cache(image_hash: str) -> dict | None:
    return await asyncio.to_thread(_get_news_image_cache_sync, image_hash)


def _put_news_image_cache_sync(
    image_hash: str,
    image_url: str,
    notice_url: str,
    extracted_text: str,
    model: str,
) -> None:
    now = int(time.time())
    with closing(_news_cache_connect()) as db:
        db.execute(
            """
            INSERT INTO official_news_image_cache
                (image_hash, image_url, notice_url, extracted_text, model,
                 created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(image_hash) DO UPDATE SET
                image_url = excluded.image_url,
                notice_url = excluded.notice_url,
                extracted_text = excluded.extracted_text,
                model = excluded.model,
                last_used_at = excluded.last_used_at
            """,
            (
                str(image_hash), str(image_url or ""), str(notice_url or ""),
                str(extracted_text), str(model or ""), now, now,
            ),
        )
        db.commit()


async def put_news_image_cache(
    image_hash: str,
    image_url: str,
    notice_url: str,
    extracted_text: str,
    model: str,
) -> None:
    await asyncio.to_thread(
        _put_news_image_cache_sync,
        image_hash,
        image_url,
        notice_url,
        extracted_text,
        model,
    )


def _get_news_answer_cache_sync(
    query_key: str,
    max_age_seconds: int,
) -> str | None:
    now = int(time.time())
    with closing(_news_cache_connect()) as db:
        row = db.execute(
            "SELECT answer, created_at FROM official_news_answer_cache "
            "WHERE query_key = ?",
            (str(query_key),),
        ).fetchone()
        if not row or now - int(row["created_at"]) > int(max_age_seconds):
            return None
        db.execute(
            "UPDATE official_news_answer_cache SET last_used_at = ? "
            "WHERE query_key = ?",
            (now, str(query_key)),
        )
        db.commit()
        return str(row["answer"])


async def get_news_answer_cache(
    query_key: str,
    max_age_seconds: int,
) -> str | None:
    return await asyncio.to_thread(
        _get_news_answer_cache_sync,
        query_key,
        max_age_seconds,
    )


def _put_news_answer_cache_sync(
    query_key: str,
    question: str,
    answer: str,
) -> None:
    now = int(time.time())
    with closing(_news_cache_connect()) as db:
        db.execute(
            """
            INSERT INTO official_news_answer_cache
                (query_key, question, answer, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(query_key) DO UPDATE SET
                question = excluded.question,
                answer = excluded.answer,
                created_at = excluded.created_at,
                last_used_at = excluded.last_used_at
            """,
            (str(query_key), str(question), str(answer), now, now),
        )
        db.commit()


async def put_news_answer_cache(
    query_key: str,
    question: str,
    answer: str,
) -> None:
    await asyncio.to_thread(
        _put_news_answer_cache_sync,
        query_key,
        question,
        answer,
    )

RESISTANCE_MULTIPLIERS = {
    "fatal": "x2",
    "weak": "x1.5",
    "normal": "x1",
    "endure": "x0.75",
    "endured": "x0.75",
    "ineff": "x0.5",
    "ineffective": "x0.5",
}

# Alias cộng đồng không phải lúc nào cũng tồn tại dưới dạng redirect trên wiki.
# Danh sách nhỏ này chỉ mở rộng query; nguồn trả lời vẫn luôn là trang wiki thật.
COMMUNITY_ALIASES = {
    "nclair": "The One Who Shall Grip Sinclair",
    "n faust": "The One Who Grips Faust",
    "lord honglu": "The Lord of Hongyuan Hong Lu",
    "lordlu": "The Lord of Hongyuan Hong Lu",
    "lord lu": "The Lord of Hongyuan Hong Lu",
    "lord hong lu": "The Lord of Hongyuan Hong Lu",
    "hongyuan honglu": "The Lord of Hongyuan Hong Lu",
    "hongyuan hong lu": "The Lord of Hongyuan Hong Lu",
    "riensang": "The House of Spiders: The Index Nursefather Yi Sang",
    "rien sang": "The House of Spiders: The Index Nursefather Yi Sang",
    "larp sang": "The House of Spiders: The Index Nursefather Yi Sang",
    "w don": "W Corp. L3 Cleanup Agent Don Quixote",
    "w ryoshu": "W Corp. L3 Cleanup Agent Ryōshū",
    "w ryōshū": "W Corp. L3 Cleanup Agent Ryōshū",
    "hos ryoshu": "Blade of the House of Spiders Ryōshū",
    "hos ryōshū": "Blade of the House of Spiders Ryōshū",
}

EXCLUDED_SECTIONS = {
    "gallery", "navigation", "references", "external links", "see also",
    "contents", "voicelines", "identity story",
}

KST = timezone(timedelta(hours=9), name="KST")
LATEST_RELEASE_MARKERS = (
    "latest", "newest", "most recent", "just released", "new release",
    "current banner", "ongoing banner", "currently available",
    "mới nhất", "mới ra", "vừa ra", "vừa mới ra", "gần đây nhất",
    "banner hiện tại", "banner đang chạy", "đang rate-up", "đang rate up",
)
RELEASE_KIND_MARKERS = (
    "identity", "identities", " id ", "id nào", "ego", "e.g.o",
    "extraction", "banner",
)
NURSEFATHER_MARKERS = ("nursefather", "nursefathers")
IDENTITY_KIT_MARKERS = (
    "full skill", "full skills", "full kit", "complete kit", "all skills",
    "toàn bộ skill", "toàn bộ kỹ năng", "đầy đủ skill", "bộ kỹ năng",
)
EGO_ROSTER_MARKERS = (
    "all ego", "all e.g.o", "ego list", "e.g.o list", "list ego", "list e.g.o",
    "tat ca ego", "tat ca e g o", "toan bo ego", "toan bo e g o",
    "cac ego", "cac e g o", "danh sach ego", "danh sach e g o",
)
IDENTITY_ROSTER_MARKERS = (
    "all id", "all ids", "all identity", "all identities",
    "id list", "ids list", "identity list", "list id", "list ids",
    "list identity", "list identities",
    "tat ca id", "tat ca identity", "tat ca identities",
    "toan bo id", "toan bo identity", "toan bo identities",
    "cac id", "cac identity", "cac identities",
    "danh sach id", "danh sach identity", "danh sach identities",
)

SINNER_NAMES = (
    "Yi Sang", "Faust", "Don Quixote", "Ryoshu", "Meursault", "Hong Lu",
    "Heathcliff", "Ishmael", "Rodion", "Sinclair", "Outis", "Gregor",
)
SINNER_QUERY_ALIASES = {
    "yi sang": "Yi Sang", "yisang": "Yi Sang",
    "faust": "Faust",
    "don quixote": "Don Quixote", "donquixote": "Don Quixote", "donqui": "Don Quixote",
    "don": "Don Quixote",
    "ryoshu": "Ryoshu",
    "meursault": "Meursault", "mersault": "Meursault",
    "hong lu": "Hong Lu", "honglu": "Hong Lu",
    "heathcliff": "Heathcliff", "heath": "Heathcliff",
    "ishmael": "Ishmael", "ish": "Ishmael",
    "rodion": "Rodion", "rodya": "Rodion",
    "sinclair": "Sinclair",
    "outis": "Outis",
    "gregor": "Gregor",
}

# Alias chỉ dùng khi người dùng đang hỏi kit/skill của Identity. "Wild Hunt"
# vẫn có thể chỉ faction/boss ở câu lore nên không đưa vào COMMUNITY_ALIASES.
IDENTITY_KIT_ALIASES = {
    "wildhunt": "Wild Hunt Heathcliff",
    "wild hunt": "Wild Hunt Heathcliff",
    "spicebush": "Effloresced E.G.O::Spicebush Yi Sang",
    "spicebush yisang": "Effloresced E.G.O::Spicebush Yi Sang",
    "captain ish": "The Pequod Captain Ishmael",
    "captain ishmael": "The Pequod Captain Ishmael",
    "k honglu": "K Corp. Class 3 Excision Staff Hong Lu",
    "k hong lu": "K Corp. Class 3 Excision Staff Hong Lu",
    "t don": "T Corp. Class 3 Collection Staff Don Quixote",
    "dieci rodya": "Dieci Assoc. South Section 4 Rodion",
    "dieci rodion": "Dieci Assoc. South Section 4 Rodion",
    "molar outis": "Molar Office Fixer Outis",
    "bl meursault": "Blade Lineage Mentor Meursault",
    "bl mersault": "Blade Lineage Mentor Meursault",
    "rabbit heath": "R Corp. 4th Pack Rabbit Heathcliff",
    "r heath": "R Corp. 4th Pack Rabbit Heathcliff",
    "pequod heath": "The Pequod Harpooneer Heathcliff",
    "harpoon heath": "The Pequod Harpooneer Heathcliff",
    "pequod yisang": "The Pequod First Mate Yi Sang",
    "ring sang": "The Ring Pointillist Student Yi Sang",
    "ring yisang": "The Ring Pointillist Student Yi Sang",
    "ring outis": "The Ring Pointillist Student Outis",
    "w heath": "W Corp. L4 Cleanup Agent - CCA Heathcliff",
    "w heathcliff": "W Corp. L4 Cleanup Agent - CCA Heathcliff",
    "cca heath": "W Corp. L4 Cleanup Agent - CCA Heathcliff",
}

# Các cách cộng đồng thường viết liền tên Sinner. Đây là mở rộng token cho
# fuzzy title matching, không ép về một Identity cụ thể như COMMUNITY_ALIASES.
COMPACT_SINNER_ALIASES = {
    "yisang": "yi sang",
    "honglu": "hong lu",
    "donquixote": "don quixote",
    "donqui": "don quixote",
    "ryoshu": "ryoshu",
    "heath": "heathcliff",
    "rodya": "rodion",
    "mersault": "meursault",
}


@dataclass(frozen=True)
class WikiPage:
    pageid: int
    title: str
    url: str
    revid: int
    timestamp: str
    text: str


def _asset_file_candidates(title: str, *, kind: str = "") -> list[str]:
    """Build the canonical file names used by IDPage/EGPage on wiki.gg."""
    exact = re.sub(r"\s+", "_", str(title or "").strip())
    no_colon = exact.replace(":", "")
    colon_as_space = re.sub(
        r"\s+", "_", re.sub(r":+", " ", str(title or "")).strip()
    )
    candidates: list[str] = []
    if kind == "identity":
        suffixes = ("_Profile.png",)
    elif kind == "ego":
        suffixes = ("_Icon.png",)
    else:
        suffixes = (
            "_Profile.png",
            "_Icon.png",
            "_Full_Uptied.png",
            "_Full.png",
        )
    for base in (exact, no_colon, colon_as_space):
        for suffix in suffixes:
            candidate = f"{base}{suffix}"
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _asset_from_imageinfo_pages(
    pages: list[dict], *, content_page: WikiPage, candidates: list[str]
) -> dict | None:
    available: dict[str, tuple[str, dict]] = {}
    for page in pages:
        info_items = page.get("imageinfo") or []
        if not info_items:
            continue
        file_title = str(page.get("title") or "").removeprefix("File:")
        available[file_title.replace(" ", "_").casefold()] = (file_title, info_items[0])
    for candidate in candidates:
        match = available.get(candidate.casefold())
        if not match:
            continue
        file_title, info = match
        original_url = str(info.get("url") or "").strip()
        thumbnail_url = str(info.get("thumburl") or original_url).strip()
        if not (thumbnail_url or original_url):
            continue
        return {
            "pageid": content_page.pageid,
            "title": content_page.title,
            "file_title": file_title,
            "original_url": original_url,
            "thumbnail_url": thumbnail_url,
            "asset_url": thumbnail_url or original_url,
        }
    return None


class _WikiHTMLTextParser(HTMLParser):
    """Chuyển HTML đã render của MediaWiki thành text có cấu trúc nhẹ."""

    _BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "caption", "dd",
        "div", "dl", "dt", "figcaption", "figure", "footer", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "p",
        "section", "table", "tbody", "td", "th", "thead", "tr", "ul", "ol",
    }
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "button", "form"}
    _SKIP_CLASSES = {
        "mw-editsection", "mw-references-wrap", "navbox", "navbar", "noprint",
        "toc", "custom-tabs", "metadata", "mw-collapsible-toggle",
    }
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self._skip_stack: list[bool] = []
        self._heading_level: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attr_map = {key.casefold(): value or "" for key, value in attrs}
        classes = set(attr_map.get("class", "").casefold().split())
        should_skip = tag in self._SKIP_TAGS or bool(classes & self._SKIP_CLASSES)
        if tag in self._VOID_TAGS and should_skip:
            return
        if tag not in self._VOID_TAGS:
            self._skip_stack.append(should_skip)
        if should_skip:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append(" | ")
        if tag == "li":
            self.parts.append("• ")
        if re.fullmatch(r"h[1-6]", tag):
            self._heading_level = int(tag[1])
            self.parts.append("#" * self._heading_level + " ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._VOID_TAGS:
            return
        was_skip = self._skip_stack.pop() if self._skip_stack else False
        if was_skip:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        if re.fullmatch(r"h[1-6]", tag):
            self._heading_level = None

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        value = html.unescape(data).replace("\xa0", " ")
        if value.strip():
            self.parts.append(value)

    def text(self) -> str:
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r" *\| *\|+ *", " | ", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        value = re.sub(r"\[edit\]", "", value, flags=re.IGNORECASE)
        return value.strip()[:MAX_PAGE_TEXT]


def _html_to_text(raw_html: str) -> str:
    parser = _WikiHTMLTextParser()
    parser.feed(raw_html or "")
    parser.close()
    return parser.text()


def _wiki_url(title: str) -> str:
    encoded = quote(str(title).replace(" ", "_"), safe="/:,").replace("'", "%27")
    return WIKI_ROOT + encoded


def _chunk_page(title: str, text: str) -> list[tuple[str, int, str]]:
    """Chia theo heading trước, sau đó theo kích thước; giữ overlap nhỏ."""
    sections: list[tuple[str, str]] = []
    section_name = "Overview"
    buffer: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^#{1,6}\s+(.+)$", line.strip())
        if heading:
            if buffer:
                sections.append((section_name, "\n".join(buffer).strip()))
            section_name = heading.group(1).strip()[:200]
            buffer = []
        else:
            buffer.append(line)
    if buffer:
        sections.append((section_name, "\n".join(buffer).strip()))

    chunks: list[tuple[str, int, str]] = []
    ordinal = 0
    for section, body in sections:
        if section.casefold().strip() in EXCLUDED_SECTIONS:
            continue
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if not body:
            continue
        cursor = 0
        while cursor < len(body):
            end = min(len(body), cursor + CHUNK_CHARS)
            if end < len(body):
                split = max(body.rfind("\n", cursor, end), body.rfind(". ", cursor, end))
                if split > cursor + CHUNK_CHARS // 2:
                    end = split + 1
            content = body[cursor:end].strip()
            if content:
                chunks.append((section or title, ordinal, content))
                ordinal += 1
            if end >= len(body):
                break
            cursor = max(cursor + 1, end - CHUNK_OVERLAP)
    return chunks


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[^\W_][\w.'’:-]{1,}", query, flags=re.UNICODE)
    tokens = [token.replace('"', "")[:80] for token in tokens[:16]]
    return " OR ".join(f'"{token}"' for token in tokens if token)


def _expand_query(query: str) -> str:
    expanded = query
    official = _alias_title(query)
    if official:
        expanded += " " + official
    return expanded[:500]


def _alias_title(query: str) -> str | None:
    # So khớp trên chuỗi đã bỏ dấu và chỉ nhận cả cụm/token. Điều này vẫn hiểu
    # RienSang/Rien Sang nhưng không vô tình bắt alias nằm giữa một từ khác.
    normalized_query = f" {_normalize_lookup(query)} "
    compact_tokens = set(normalized_query.split())
    for alias, official in sorted(
        COMMUNITY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        normalized_alias = _normalize_lookup(alias)
        if f" {normalized_alias} " in normalized_query:
            return official
        if " " in normalized_alias and normalized_alias.replace(" ", "") in compact_tokens:
            return official
    return None


def _is_latest_release_query(query: str) -> bool:
    text = f" {str(query or '').casefold()} "
    normalized = re.sub(r"\s+", " ", text)
    latest = any(marker in normalized for marker in LATEST_RELEASE_MARKERS) or (
        any(word in normalized for word in ("mới", "new", "recent"))
        and any(word in normalized for word in ("ra", "release", "released"))
    )
    return latest and any(
        marker in normalized for marker in RELEASE_KIND_MARKERS
    )


def _is_nursefather_roster_query(query: str) -> bool:
    text = str(query or "").casefold()
    if not any(marker in text for marker in NURSEFATHER_MARKERS):
        return False
    return any(
        marker in text
        for marker in (
            "how many", "list", "name", "who", "bao nhiêu", "kể tên",
            "gồm ai", "những ai", "là ai",
        )
    )


def _is_identity_kit_query(query: str) -> bool:
    text = str(query or "").casefold()
    return any(marker in text for marker in IDENTITY_KIT_MARKERS)


def _is_ego_roster_query(query: str) -> bool:
    normalized = f" {_normalize_lookup(query)} "
    has_ego = " ego " in normalized or " e g o " in normalized
    return has_ego and any(marker in normalized for marker in EGO_ROSTER_MARKERS)


def _is_identity_roster_query(query: str) -> bool:
    normalized = f" {_normalize_lookup(query)} "
    has_identity = bool(
        re.search(r"\b(?:id|ids|identity|identities)\b", normalized)
    )
    return has_identity and any(
        f" {marker} " in normalized for marker in IDENTITY_ROSTER_MARKERS
    )


def _is_ego_detail_query(query: str) -> bool:
    normalized = f" {_normalize_lookup(query)} "
    return (
        (" ego " in normalized or " e g o " in normalized)
        and not _is_ego_roster_query(query)
        and not _is_latest_release_query(query)
    )


def _sinner_from_query(query: str) -> str | None:
    normalized = f" {_normalize_lookup(query)} "
    tokens = set(normalized.split())
    for alias, sinner in sorted(
        SINNER_QUERY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if f" {alias} " in normalized:
            return sinner
        if " " in alias and alias.replace(" ", "") in tokens:
            return sinner
    return None


def _requested_identity_skill_slot(query: str) -> str | None:
    """Nhận S1/S2/S3/Defense; Skill 3 mặc định không kéo theo biến thể 3-2."""
    normalized = _normalize_lookup(query)
    if not normalized or _is_identity_kit_query(query):
        return None
    if re.search(
        r"\b(?:defense|defence|defensive|evade|skill thu|ky nang thu)\b",
        normalized,
    ):
        return "defense"
    match = re.search(
        r"\b(?:skill|ky nang|s)\s*([123])(?:\s+([23]))?\b",
        normalized,
    )
    if not match:
        return None
    base, variant = match.groups()
    if variant and base in {"1", "2", "3"}:
        return f"skill{base}-{variant}"
    return f"skill{base}"


def _normalize_lookup(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(
        re.sub(r"[^a-z0-9]+", " ", "".join(ch for ch in value if not unicodedata.combining(ch))).split()
    )


def _extract_assigned_template(wikitext: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\|{re.escape(key)}\s*=\s*(\{{\{{)", wikitext)
    if not match:
        return None
    start = match.start(1)
    depth = 0
    index = start
    while index < len(wikitext) - 1:
        pair = wikitext[index:index + 2]
        if pair == "{{":
            depth += 1
            index += 2
            continue
        if pair == "}}":
            depth -= 1
            index += 2
            if depth == 0:
                return wikitext[start:index]
            continue
        index += 1
    return None


def _template_params(template: str) -> dict[str, str]:
    """Tách tham số cấp cao nhất của template MediaWiki, giữ template con."""
    if not template.startswith("{{"):
        return {}
    body = template[2:-2]
    first_newline = body.find("\n")
    if first_newline < 0:
        return {}
    body = body[first_newline + 1:]
    starts: list[int] = []
    curly = square = 0
    line_start = True
    index = 0
    while index < len(body):
        pair = body[index:index + 2]
        if pair == "{{":
            curly += 1
            index += 2
            line_start = False
            continue
        if pair == "}}":
            curly = max(0, curly - 1)
            index += 2
            line_start = False
            continue
        if pair == "[[":
            square += 1
            index += 2
            line_start = False
            continue
        if pair == "]]":
            square = max(0, square - 1)
            index += 2
            line_start = False
            continue
        char = body[index]
        if line_start and char == "|" and curly == 0 and square == 0:
            starts.append(index)
        line_start = char == "\n"
        index += 1

    params: dict[str, str] = {}
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(body)
        item = body[start + 1:end].strip()
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        params[name.strip()] = value.strip()
    return params


def _split_template_arguments(template: str) -> tuple[list[str], dict[str, str]]:
    body = template[2:-2] if template.startswith("{{") else template
    parts: list[str] = []
    start = 0
    curly = square = 0
    index = 0
    while index < len(body):
        pair = body[index:index + 2]
        if pair == "{{":
            curly += 1
            index += 2
            continue
        if pair == "}}":
            curly = max(0, curly - 1)
            index += 2
            continue
        if pair == "[[":
            square += 1
            index += 2
            continue
        if pair == "]]":
            square = max(0, square - 1)
            index += 2
            continue
        if body[index] == "|" and curly == 0 and square == 0:
            parts.append(body[start:index].strip())
            start = index + 1
        index += 1
    parts.append(body[start:].strip())
    positional: list[str] = []
    named: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            if re.fullmatch(r"[\w-]+", key.strip()):
                named[key.strip()] = value.strip()
                continue
        positional.append(part)
    return positional, named


def _extract_templates_from_region(region: str, name: str) -> list[str]:
    results: list[str] = []
    pattern = re.compile(r"\{\{" + re.escape(name) + r"(?=[\s|}])", re.IGNORECASE)
    for match in pattern.finditer(region):
        start = match.start()
        depth = 0
        index = start
        while index < len(region) - 1:
            pair = region[index:index + 2]
            if pair == "{{":
                depth += 1
                index += 2
                continue
            if pair == "}}":
                depth -= 1
                index += 2
                if depth == 0:
                    results.append(region[start:index])
                    break
                continue
            index += 1
    return results


def _plain_wikitext(value: str) -> str:
    value = str(value or "")
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"<!--[\s\S]*?-->", "", value)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</?(?:b|i|small|span)[^>]*>", "", value, flags=re.IGNORECASE)
    value = value.replace("'''", "").replace("''", "")
    value = re.sub(r"\[\[:?[^\]|]+\|([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\{\{SkillCon\|([^{}|]+)(?:\|[^{}]*)?\}\}", r"[\1]", value)
    value = re.sub(r"\{\{StatusEffect\|([^{}|]+)(?:\|[^{}]*)?\}\}", r"\1", value)
    value = re.sub(r"\{\{(?:Ryoshu|Ryōshū)\}\}", "Ryōshū", value, flags=re.IGNORECASE)
    value = re.sub(r"\{\{Icons?\|([^{}|]+)(?:\|[^{}]*)?\}\}", r"\1", value)
    value = re.sub(r"\{\{Keyword\|([^{}|]+)(?:\|[^{}]*)?\}\}", r"\1", value)
    value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip().strip('"')


def _parse_kst_banner_time(value: str) -> datetime:
    value = value.strip()
    pattern = "%Y.%m.%d %H:%M" if ":" in value else "%Y.%m.%d"
    return datetime.strptime(value, pattern).replace(tzinfo=KST)


def _parse_latest_banner(text: str, *, now: datetime | None = None) -> dict | None:
    """Đọc banner mới/ph đang chạy từ bảng Banner History đã làm sạch."""
    if not text:
        return None
    now = (now or datetime.now(KST)).astimezone(KST)
    past_section = text.split("## Past Banners", 1)[-1]
    date_pattern = re.compile(
        r"(?P<start>20\d{2}\.\d{1,2}\.\d{1,2}(?:\s+\d{1,2}:\d{2})?)"
        r"\s*-\s*"
        r"(?P<end>20\d{2}\.\d{1,2}\.\d{1,2}(?:\s+\d{1,2}:\d{2})?)"
    )
    matches = list(date_pattern.finditer(past_section))
    banners: list[dict] = []
    for index, match in enumerate(matches):
        try:
            start = _parse_kst_banner_time(match.group("start"))
            end = _parse_kst_banner_time(match.group("end"))
        except ValueError:
            continue
        segment_end = matches[index + 1].start() if index + 1 < len(matches) else len(past_section)
        segment = past_section[match.end():segment_end]
        items: list[str] = []
        for raw_line in segment.splitlines():
            candidate = raw_line.strip().strip("|").strip()
            if not candidate or candidate.startswith("Season "):
                continue
            if candidate not in items:
                items.append(candidate)
        if items:
            banners.append({"start": start, "end": end, "items": items})

    if not banners:
        return None
    active = [entry for entry in banners if entry["start"] <= now < entry["end"]]
    chosen = max(active, key=lambda entry: entry["start"]) if active else max(
        (entry for entry in banners if entry["start"] <= now),
        key=lambda entry: entry["start"],
        default=None,
    )
    if not chosen:
        return None
    return {
        **chosen,
        "active": chosen["start"] <= now < chosen["end"],
        "as_of": now,
    }


class LimbusWiki(commands.Cog):
    """Kho kiến thức Limbus Company tự đồng bộ từ wiki.gg."""

    limbusasset = app_commands.Group(
        name="limbusasset",
        description="Artwork Identity/E.G.O đã đồng bộ từ Limbus Company Wiki",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.sync_task: asyncio.Task | None = None
        self.db_lock = asyncio.Lock()
        self.sync_lock = asyncio.Lock()
        self.request_lock = asyncio.Lock()
        self.last_request_at = 0.0
        self.asset_tasks: set[asyncio.Task] = set()

    async def cog_load(self) -> None:
        await asyncio.to_thread(self._init_db_sync)
        timeout = aiohttp.ClientTimeout(total=45, connect=15, sock_read=35)
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self.sync_task = asyncio.create_task(
            self._sync_loop(), name="limbus-wiki-sync"
        )

    async def cog_unload(self) -> None:
        for task in tuple(self.asset_tasks):
            task.cancel()
        if self.asset_tasks:
            await asyncio.gather(*self.asset_tasks, return_exceptions=True)
        self.asset_tasks.clear()
        if self.sync_task:
            self.sync_task.cancel()
            try:
                await self.sync_task
            except asyncio.CancelledError:
                pass
        if self.session:
            await self.session.close()
        self.session = None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(DB_PATH, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _init_db_sync(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wiki_pages (
                    pageid INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    revid INTEGER NOT NULL,
                    timestamp TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    indexed_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wiki_pages_title
                    ON wiki_pages(title COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS wiki_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS wiki_chunks USING fts5(
                    pageid UNINDEXED,
                    title,
                    section,
                    content,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )
            db.executescript(OFFICIAL_NEWS_CACHE_SCHEMA)
            db.executescript(ASSET_CACHE_SCHEMA)
            current = db.execute(
                "SELECT value FROM wiki_meta WHERE key='index_version'"
            ).fetchone()
            if not current or str(current[0]) != INDEX_VERSION:
                # Parser/chunking đổi thì revision trên wiki vẫn giữ nguyên. Xóa index
                # cũ để lần sync kế tiếp thực sự dựng lại nội dung thay vì tưởng đã mới.
                db.execute("DELETE FROM wiki_chunks")
                db.execute("DELETE FROM wiki_pages")
                db.execute("DELETE FROM wiki_meta WHERE key != 'index_version'")
                db.execute(
                    "INSERT INTO wiki_meta(key, value) VALUES('index_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (INDEX_VERSION,),
                )

    async def _api_get(self, **params) -> dict:
        if not self.session:
            raise RuntimeError("Limbus Wiki HTTP session chưa sẵn sàng")
        params.update({"format": "json", "formatversion": 2, "maxlag": 5})
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                async with self.request_lock:
                    wait = REQUEST_DELAY - (time.monotonic() - self.last_request_at)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self.last_request_at = time.monotonic()
                async with self.session.get(API_URL, params=params) as response:
                    if response.status in {429, 502, 503, 504}:
                        raise RuntimeError(f"MediaWiki HTTP {response.status}")
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                if "error" in payload:
                    raise RuntimeError(str(payload["error"]))
                return payload
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as error:
                last_error = error
                if attempt >= 3:
                    break
                await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError("Không thể đọc Limbus Company Wiki API") from last_error

    def _cached_asset_sync(
        self, pageid: int, *, revid: int | None = None
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT pageid, title, revid, file_title, original_url, "
                "thumbnail_url, synced_at FROM wiki_assets WHERE pageid = ?",
                (pageid,),
            ).fetchone()
        if not row or (revid is not None and int(row["revid"]) != int(revid)):
            return None
        result = dict(row)
        result["asset_url"] = result["thumbnail_url"] or result["original_url"]
        return result

    def _upsert_asset_sync(self, asset: dict, revid: int) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO wiki_assets(
                    pageid, title, revid, file_title, original_url,
                    thumbnail_url, synced_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pageid) DO UPDATE SET
                    title=excluded.title,
                    revid=excluded.revid,
                    file_title=excluded.file_title,
                    original_url=excluded.original_url,
                    thumbnail_url=excluded.thumbnail_url,
                    synced_at=excluded.synced_at
                """,
                (
                    int(asset.get("pageid") or 0),
                    str(asset.get("title") or ""),
                    int(revid or 0),
                    str(asset.get("file_title") or ""),
                    str(asset.get("original_url") or ""),
                    str(asset.get("thumbnail_url") or ""),
                    int(time.time()),
                ),
            )

    def _asset_status_sync(self) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN thumbnail_url != '' OR original_url != '' THEN 1 ELSE 0 END) "
                "AS with_image, MAX(synced_at) AS last_sync FROM wiki_assets"
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "with_image": int(row["with_image"] or 0),
            "last_sync": int(row["last_sync"] or 0),
        }

    def _missing_asset_pages_sync(self) -> list[tuple[WikiPage, str]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT p.pageid, p.title, p.url, p.revid, p.timestamp, p.text
                FROM wiki_pages AS p
                LEFT JOIN wiki_assets AS a ON a.pageid = p.pageid
                WHERE p.title NOT LIKE '%/%'
                  AND (a.pageid IS NULL OR a.revid != p.revid)
                  AND (
                    p.text LIKE '%Skill 1Skill 2Skill 3Defense%'
                    OR (
                        p.text LIKE '%Risk Level%'
                        AND p.text NOT LIKE '%Skill 1Skill 2Skill 3Defense%'
                    )
                  )
                ORDER BY p.title COLLATE NOCASE
                """
            ).fetchall()
        result: list[tuple[WikiPage, str]] = []
        for row in rows:
            text = str(row["text"])
            kind = "identity" if "Skill 1Skill 2Skill 3Defense" in text else "ego"
            result.append((
                WikiPage(
                    pageid=int(row["pageid"]),
                    title=str(row["title"]),
                    url=str(row["url"]),
                    revid=int(row["revid"]),
                    timestamp=str(row["timestamp"]),
                    text=text,
                ),
                kind,
            ))
        return result

    async def _sync_missing_assets(self) -> tuple[int, int]:
        entries = await asyncio.to_thread(self._missing_asset_pages_sync)
        if not entries:
            return 0, 0
        completed = 0
        failed = 0
        # Mỗi trang chỉ tạo tối đa hai tên file. Dùng batch nhỏ vì tên Identity
        # có thể rất dài; như vậy URL GET không chạm giới hạn proxy/CDN dù số
        # title vẫn thấp hơn mức 50 của MediaWiki.
        batch_size = 8
        for offset in range(0, len(entries), batch_size):
            batch = entries[offset: offset + batch_size]
            all_candidates: list[str] = []
            candidates_by_page: dict[int, list[str]] = {}
            for page, kind in batch:
                candidates = _asset_file_candidates(page.title, kind=kind)
                candidates_by_page[page.pageid] = candidates
                all_candidates.extend(candidates)
            try:
                payload = await self._api_get(
                    action="query",
                    titles="|".join(f"File:{name}" for name in all_candidates),
                    prop="imageinfo",
                    iiprop="url|mime",
                    iiurlwidth=ASSET_THUMB_SIZE,
                )
                image_pages = payload.get("query", {}).get("pages", [])
                for page, _kind in batch:
                    asset = _asset_from_imageinfo_pages(
                        image_pages,
                        content_page=page,
                        candidates=candidates_by_page[page.pageid],
                    ) or {
                        "pageid": page.pageid,
                        "title": page.title,
                        "file_title": "",
                        "original_url": "",
                        "thumbnail_url": "",
                        "asset_url": "",
                    }
                    await asyncio.to_thread(self._upsert_asset_sync, asset, page.revid)
                    completed += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                failed += len(batch)
                logger.warning(
                    "Không thể đồng bộ batch Limbus asset %s-%s",
                    offset + 1,
                    offset + len(batch),
                    exc_info=True,
                )
        logger.info(
            "Limbus Asset sync xong: cập nhật=%s, lỗi=%s", completed, failed
        )
        return completed, failed

    def _queue_asset_refresh(self, page: WikiPage, *, kind: str = "") -> None:
        if any(task.get_name() == f"limbus-asset-{page.pageid}" for task in self.asset_tasks):
            return
        task = asyncio.create_task(
            self._page_asset(page, force=True, kind=kind),
            name=f"limbus-asset-{page.pageid}",
        )
        self.asset_tasks.add(task)
        task.add_done_callback(self.asset_tasks.discard)

    async def _page_asset(
        self, page: WikiPage, *, force: bool = False, kind: str = ""
    ) -> dict | None:
        if not force:
            cached = await asyncio.to_thread(
                self._cached_asset_sync, page.pageid, revid=page.revid
            )
            if cached:
                return cached if cached.get("asset_url") else None

        stale = await asyncio.to_thread(self._cached_asset_sync, page.pageid)
        if not force:
            # Không bắt người chat chờ wiki/CDN. Lượt đầu có thể chưa có thumbnail,
            # nhưng refresh tiếp tục nền và các lượt sau dùng cache ngay lập tức.
            self._queue_asset_refresh(page, kind=kind)
            return stale if stale and stale.get("asset_url") else None
        try:
            candidates = _asset_file_candidates(page.title, kind=kind)
            payload = await self._api_get(
                action="query",
                titles="|".join(f"File:{name}" for name in candidates),
                prop="imageinfo",
                iiprop="url|mime",
                iiurlwidth=ASSET_THUMB_SIZE,
            )
            pages = payload.get("query", {}).get("pages", [])
            asset = _asset_from_imageinfo_pages(
                pages, content_page=page, candidates=candidates
            )
            if not asset:
                # Cache cả kết quả "không có" theo revision để không gọi wiki lại ở
                # mọi câu hỏi. Revision đổi sẽ tự thử lại.
                asset = {
                    "pageid": page.pageid,
                    "title": page.title,
                    "file_title": "",
                    "original_url": "",
                    "thumbnail_url": "",
                    "asset_url": "",
                }
            await asyncio.to_thread(self._upsert_asset_sync, asset, page.revid)
            return asset if asset.get("asset_url") else None
        except Exception:
            # Ảnh chỉ là phần trình bày; wiki lỗi không được làm hỏng câu trả lời kit.
            logger.warning(
                "Không thể làm mới Limbus asset: %s; dùng cache cũ nếu có",
                page.title,
                exc_info=True,
            )
            return stale if stale and stale.get("asset_url") else None

    async def _catalog(self) -> list[dict]:
        pages: list[dict] = []
        continuation: dict = {}
        while True:
            payload = await self._api_get(
                action="query",
                generator="allpages",
                gapnamespace=0,
                gapfilterredir="nonredirects",
                gaplimit="max",
                prop="revisions|info",
                rvprop="ids|timestamp",
                inprop="url",
                **continuation,
            )
            pages.extend(payload.get("query", {}).get("pages", []))
            continuation = payload.get("continue") or {}
            if not continuation:
                return pages

    async def _fetch_page(self, title: str, *, pageid: int = 0, timestamp: str = "") -> WikiPage:
        payload = await self._api_get(
            action="parse",
            page=title,
            prop="text|revid|displaytitle",
            disableeditsection=1,
            disabletoc=1,
        )
        parsed = payload.get("parse") or {}
        normalized_title = str(parsed.get("title") or title)
        text = _html_to_text(str(parsed.get("text") or ""))
        if len(text) < 40:
            raise RuntimeError(f"Trang {normalized_title!r} không có nội dung dùng được")
        resolved_pageid = int(parsed.get("pageid") or pageid or 0)
        url = _wiki_url(normalized_title)
        return WikiPage(
            pageid=resolved_pageid,
            title=normalized_title,
            url=url,
            revid=int(parsed.get("revid") or 0),
            timestamp=timestamp,
            text=text,
        )

    def _upsert_page_sync(self, page: WikiPage) -> int:
        chunks = _chunk_page(page.title, page.text)
        with self._connect() as db:
            db.execute("DELETE FROM wiki_chunks WHERE pageid = ?", (page.pageid,))
            db.execute(
                """
                INSERT INTO wiki_pages(pageid, title, url, revid, timestamp, text, indexed_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pageid) DO UPDATE SET
                    title=excluded.title, url=excluded.url, revid=excluded.revid,
                    timestamp=excluded.timestamp, text=excluded.text,
                    indexed_at=excluded.indexed_at
                """,
                (
                    page.pageid, page.title, page.url, page.revid, page.timestamp,
                    page.text, int(time.time()),
                ),
            )
            db.executemany(
                "INSERT INTO wiki_chunks(pageid, title, section, content) VALUES(?, ?, ?, ?)",
                [
                    (page.pageid, page.title, section, content)
                    for section, _ordinal, content in chunks
                ],
            )
        return len(chunks)

    async def _upsert_page(self, page: WikiPage) -> int:
        async with self.db_lock:
            return await asyncio.to_thread(self._upsert_page_sync, page)

    def _known_revisions_sync(self) -> dict[int, int]:
        with self._connect() as db:
            return {
                int(row["pageid"]): int(row["revid"])
                for row in db.execute("SELECT pageid, revid FROM wiki_pages")
            }

    def _delete_missing_sync(self, live_ids: set[int]) -> int:
        with self._connect() as db:
            existing = {
                int(row[0]) for row in db.execute("SELECT pageid FROM wiki_pages")
            }
            missing = existing - live_ids
            for pageid in missing:
                db.execute("DELETE FROM wiki_chunks WHERE pageid = ?", (pageid,))
                db.execute("DELETE FROM wiki_pages WHERE pageid = ?", (pageid,))
            return len(missing)

    def _set_meta_sync(self, key: str, value: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO wiki_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    @staticmethod
    def _nursefather_context(page: WikiPage) -> dict:
        member_block = page.text.split("## Members", 1)[-1]
        member_block = member_block.split("## Overview", 1)[0]
        roster: list[dict] = []
        pattern = re.compile(
            r"(?m)^•\s*(?P<name>.+?)\s*-\s*\((?P<role>(?:Former\s+)?(?:Thumb|Index|Middle|Ring|Pinky)\s+Nursefather)\)\s*$"
        )
        for match in pattern.finditer(member_block):
            entry = {
                "name": match.group("name").strip(),
                "role": match.group("role").strip(),
            }
            if entry not in roster:
                roster.append(entry)

        sinner_block = page.text.split("## Sinner Identities", 1)[-1]
        sinner_block = sinner_block.split("## Associated E.G.O Gifts", 1)[0]
        sinner_identities: list[dict] = []
        lines = [line.strip().strip("|").strip() for line in sinner_block.splitlines()]
        for index, line in enumerate(lines[:-1]):
            role = lines[index + 1]
            if not line or not re.fullmatch(
                r"(?:Thumb|Index|Middle|Ring|Pinky) Nursefather", role
            ):
                continue
            entry = {"sinner": line, "role": role}
            if entry not in sinner_identities:
                sinner_identities.append(entry)

        core = [entry for entry in roster if not entry["role"].startswith("Former ")]
        original = [
            entry for entry in roster
            if entry["name"] in {"Valencina", "Rien", "Matthias", "Callisto", "Shiomi Yoru"}
        ]
        return {
            "type": "nursefather_roster",
            "answering_note": (
                "Nói rõ phạm vi: House ban đầu có 5 Nursefathers; nếu đếm mọi cá nhân "
                "từng giữ danh hiệu thì roster này có 6 vì Araya kế nhiệm Pinky Nursefather. "
                "Không được trả lời kiểu 'đang tra thêm'."
            ),
            "original_count": len(original),
            "original_nursefathers": original,
            "all_named_holders_count": len(roster),
            "all_named_holders": roster,
            "current_or_successor_count": len(core),
            "sinner_identity_count": len(sinner_identities),
            "sinner_identities": sinner_identities,
            "source": page.url,
        }

    async def _nursefather_roster(self) -> dict | None:
        title = "The House of Spiders"
        page = await asyncio.to_thread(self._page_by_title_sync, title)
        if not page:
            try:
                page = await self._fetch_page(title)
                await self._upsert_page(page)
            except Exception:
                logger.exception("Không thể lấy roster Nursefather")
                return None
        return self._nursefather_context(page)

    def _page_by_title_sync(self, title: str) -> WikiPage | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT pageid, title, url, revid, timestamp, text "
                "FROM wiki_pages WHERE title = ? COLLATE NOCASE",
                (title,),
            ).fetchone()
        if not row:
            return None
        return WikiPage(
            pageid=int(row["pageid"]),
            title=str(row["title"]),
            url=str(row["url"]),
            revid=int(row["revid"]),
            timestamp=str(row["timestamp"]),
            text=str(row["text"]),
        )

    def _find_identity_page_sync(self, query: str) -> WikiPage | None:
        # Alias cộng đồng là tín hiệu mạnh hơn fuzzy scoring. Nếu không ưu tiên,
        # "RienSang" có thể bị tách thành boss Rien thay vì Identity của Yi Sang.
        alias_title = _alias_title(query)
        if alias_title:
            aliased_page = self._page_by_title_sync(alias_title)
            if aliased_page:
                return aliased_page
        normalized_query = _normalize_lookup(query)
        padded_query = f" {normalized_query} "
        compact_tokens = set(normalized_query.split())
        for alias, official in sorted(
            IDENTITY_KIT_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
        ):
            normalized_alias = _normalize_lookup(alias)
            matched = f" {normalized_alias} " in padded_query
            if " " in normalized_alias:
                matched = matched or normalized_alias.replace(" ", "") in compact_tokens
            if matched:
                aliased_page = self._page_by_title_sync(official)
                if aliased_page:
                    return aliased_page
        query_tokens = {
            token for token in normalized_query.split()
            if token not in {
                "full", "skill", "skills", "kit", "all", "complete", "please",
                "di", "nhe", "ban", "cho", "toi", "cua", "identity", "ky", "nang",
                "la", "gi", "of", "the", "1", "2", "3",
            }
        }
        aliases = {
            "hos": "house of spiders",
            **COMPACT_SINNER_ALIASES,
        }
        expanded: set[str] = set()
        for token in query_tokens:
            if token in aliases:
                expanded.update(aliases[token].split())
            else:
                expanded.add(token)
        if not expanded:
            return None
        with self._connect() as db:
            rows = db.execute(
                "SELECT pageid, title, url, revid, timestamp, text FROM wiki_pages "
                "WHERE title NOT LIKE '%/%'"
            ).fetchall()
        best: tuple[int, sqlite3.Row] | None = None
        for row in rows:
            title_norm = _normalize_lookup(row["title"])
            title_tokens = set(title_norm.split())
            score = sum(3 if token in title_tokens else 1 if token in title_norm else 0 for token in expanded)
            if "skill 1skill 2skill 3defense" in str(row["text"]).casefold():
                score += 2
            if best is None or score > best[0]:
                best = (score, row)
        if not best or best[0] < max(3, len(expanded)):
            return None
        row = best[1]
        return WikiPage(
            pageid=int(row["pageid"]), title=str(row["title"]), url=str(row["url"]),
            revid=int(row["revid"]), timestamp=str(row["timestamp"]), text=str(row["text"]),
        )

    def _ego_pages_for_sinner_sync(self, sinner: str) -> list[WikiPage]:
        """Return true E.G.O pages, excluding Identities whose title contains E.G.O."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT pageid, title, url, revid, timestamp, text FROM wiki_pages "
                "WHERE title LIKE ? COLLATE NOCASE "
                "AND title NOT LIKE '%/%' AND title NOT LIKE '%::%' "
                "AND text LIKE '%Risk Level%' "
                "AND text NOT LIKE '%Skill 1Skill 2Skill 3Defense%' "
                "ORDER BY title COLLATE NOCASE",
                (f"% {sinner}",),
            ).fetchall()
        return [
            WikiPage(
                pageid=int(row["pageid"]), title=str(row["title"]), url=str(row["url"]),
                revid=int(row["revid"]), timestamp=str(row["timestamp"]), text=str(row["text"]),
            )
            for row in rows
        ]

    def _identity_pages_for_sinner_sync(self, sinner: str) -> list[WikiPage]:
        """Return every playable Identity for one Sinner from parsed wiki pages."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT pageid, title, url, revid, timestamp, text FROM wiki_pages "
                "WHERE title LIKE ? COLLATE NOCASE AND title NOT LIKE '%/%' "
                "AND text LIKE '%Skill 1Skill 2Skill 3Defense%' "
                "ORDER BY title COLLATE NOCASE",
                (f"% {sinner}",),
            ).fetchall()
        return [
            WikiPage(
                pageid=int(row["pageid"]), title=str(row["title"]), url=str(row["url"]),
                revid=int(row["revid"]), timestamp=str(row["timestamp"]), text=str(row["text"]),
            )
            for row in rows
        ]

    def _find_ego_page_sync(self, query: str) -> WikiPage | None:
        normalized = _normalize_lookup(query)
        ignored = {
            "ego", "e", "g", "o", "full", "kit", "skill", "skills", "all",
            "please", "cho", "toi", "cua", "la", "gi", "di", "nhe", "ban",
            "awakening", "awaken", "corrosion", "passive",
        }
        wanted = {token for token in normalized.split() if token not in ignored}
        if not wanted:
            return None
        with self._connect() as db:
            rows = db.execute(
                "SELECT pageid, title, url, revid, timestamp, text FROM wiki_pages "
                "WHERE title NOT LIKE '%/%' AND title NOT LIKE '%::%' "
                "AND text LIKE '%Risk Level%' "
                "AND text NOT LIKE '%Skill 1Skill 2Skill 3Defense%'"
            ).fetchall()
        best: tuple[int, sqlite3.Row] | None = None
        for row in rows:
            title_norm = _normalize_lookup(row["title"])
            title_tokens = set(title_norm.split())
            score = sum(
                4 if token in title_tokens else 1 if token in title_norm else 0
                for token in wanted
            )
            # Missing a named Sinner is a strong sign that this is another character's E.G.O.
            sinner = _sinner_from_query(query)
            if sinner and title_norm.endswith(_normalize_lookup(sinner)):
                score += 5
            if best is None or score > best[0]:
                best = (score, row)
        if not best or best[0] < max(5, len(wanted) * 2):
            return None
        row = best[1]
        return WikiPage(
            pageid=int(row["pageid"]), title=str(row["title"]), url=str(row["url"]),
            revid=int(row["revid"]), timestamp=str(row["timestamp"]), text=str(row["text"]),
        )

    async def _fetch_wikitext(self, pageid: int) -> str:
        payload = await self._api_get(
            action="parse", pageid=pageid, prop="wikitext"
        )
        return str((payload.get("parse") or {}).get("wikitext") or "")

    async def _roster_template_values(
        self, pages: list[WikiPage], field: str
    ) -> dict[int, str]:
        """Fetch one top-level template value for a whole roster in one API call."""
        if not pages:
            return {}
        payload = await self._api_get(
            action="query",
            pageids="|".join(str(page.pageid) for page in pages),
            prop="revisions",
            rvprop="content",
            rvslots="main",
        )
        values: dict[int, str] = {}
        for item in payload.get("query", {}).get("pages", []):
            pageid = int(item.get("pageid") or 0)
            revisions = item.get("revisions") or []
            if not pageid or not revisions:
                continue
            revision = revisions[0]
            main = (revision.get("slots") or {}).get("main") or {}
            wikitext = str(main.get("content") or revision.get("content") or "")
            match = re.search(
                rf"(?m)^\|{re.escape(field)}\s*=\s*([^\n]+)", wikitext
            )
            if match:
                values[pageid] = _plain_wikitext(match.group(1)).strip()
        return values

    async def _fetch_identity_source(self, pageid: int) -> tuple[str, str]:
        """Lấy template thô và HTML status trong cùng một request MediaWiki."""
        payload = await self._api_get(
            action="parse", pageid=pageid, prop="wikitext|text"
        )
        parsed = payload.get("parse") or {}
        return str(parsed.get("wikitext") or ""), str(parsed.get("text") or "")

    @staticmethod
    def _ego_resistances(params: dict[str, str]) -> list[dict]:
        result: list[dict] = []
        for sin in ("wrath", "lust", "sloth", "gluttony", "gloom", "pride", "envy"):
            raw = _plain_wikitext(params.get(f"{sin}res", "")).strip()
            if not raw:
                continue
            normalized = raw.casefold().rstrip(".")
            result.append({
                "sin": sin.title(),
                "rating": "Ineffective" if normalized in {"ineff", "ineffective"} else raw,
                "multiplier": RESISTANCE_MULTIPLIERS.get(normalized, "?"),
            })
        return result

    @staticmethod
    def _ego_passive(template: str) -> dict | None:
        positional, named = _split_template_arguments(template)
        if len(positional) < 2:
            return None
        raw_name = _plain_wikitext(positional[0])
        name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw_name)
        return {
            "name": name or "E.G.O Passive",
            "effect": _plain_wikitext(positional[1]),
            "sin": str(named.get("sin") or "None").title(),
            "requirement": _plain_wikitext(named.get("req", "")),
        }

    async def _ego_detail_context(self, query: str) -> dict | None:
        page = await asyncio.to_thread(self._find_ego_page_sync, query)
        if not page:
            return None
        try:
            wikitext = await self._fetch_wikitext(page.pageid)
        except Exception:
            logger.exception("Không thể lấy wikitext E.G.O: %s", page.title)
            return None
        asset = await self._page_asset(page, kind="ego")
        outer_templates = _extract_templates_from_region(wikitext, "EGPage")
        if not outer_templates:
            return None
        params = _template_params(outer_templates[0])
        skills: list[dict] = []
        for key, label in (("askill", "Awakening"), ("cskill", "Corrosion")):
            raw_value = params.get(key, "")
            templates = _extract_templates_from_region(raw_value, "Skill")
            if not templates:
                continue
            # The outer EGPage parameter also contains the HTML comment that
            # introduces the next Threadspin block. Parse only the exact nested
            # Skill template so "}}"/comments cannot leak into the last effect.
            skill = self._skill_from_template(key, templates[0])
            skill["label"] = label
            skills.append(skill)
        if not skills:
            return None
        passive_templates = _extract_templates_from_region(
            params.get("passive", ""), "Passive"
        )
        passive = self._ego_passive(passive_templates[0]) if passive_templates else None
        costs = []
        for sin in ("wrath", "lust", "sloth", "gluttony", "gloom", "pride", "envy"):
            amount = re.sub(r"\D", "", params.get(f"{sin}cost", ""))
            if amount:
                costs.append({"sin": sin.title(), "amount": int(amount)})
        return {
            "type": "ego_detail",
            "title": page.title,
            "url": _wiki_url(page.title),
            "asset_url": str((asset or {}).get("asset_url") or ""),
            "asset_original_url": str((asset or {}).get("original_url") or ""),
            "asset_file": str((asset or {}).get("file_title") or ""),
            "name": _plain_wikitext(params.get("prefix", "")) or skills[0].get("name"),
            "sinner": _plain_wikitext(params.get("sinner", "")),
            "risk": _plain_wikitext(params.get("risk", "")),
            "affinity": _plain_wikitext(params.get("affinity", "")),
            "season": _plain_wikitext(params.get("season", "")),
            "release_date": _plain_wikitext(params.get("releasedate", "")),
            "obtained": _plain_wikitext(params.get("obtained", "")),
            "abnormality": _plain_wikitext(params.get("abnormality", "")),
            "awakening_sanity": _plain_wikitext(params.get("asanity", "")),
            "corrosion_sanity": _plain_wikitext(params.get("csanity", "")),
            "costs": costs,
            "resistances": self._ego_resistances(params),
            "skills": skills,
            "passives": [passive] if passive else [],
            "note": "Thông số lấy ở Threadspin cao nhất đang ghi trong template wiki.",
        }

    async def _ego_roster_context(self, query: str) -> dict | None:
        sinner = _sinner_from_query(query)
        if not sinner:
            return None
        pages = await asyncio.to_thread(self._ego_pages_for_sinner_sync, sinner)
        if not pages:
            return None
        try:
            risk_by_page = await self._roster_template_values(pages, "risk")
        except Exception:
            logger.exception("Không thể lấy Risk Level cho E.G.O roster: %s", sinner)
            risk_by_page = {}
        entries = []
        suffix = f" {sinner}"
        for page in pages:
            name = page.title[:-len(suffix)] if page.title.casefold().endswith(suffix.casefold()) else page.title
            entries.append({
                "name": name,
                "title": page.title,
                "url": _wiki_url(page.title),
                "risk": risk_by_page.get(page.pageid),
            })
        return {
            "type": "ego_roster",
            "sinner": sinner,
            "count": len(entries),
            "entries": entries,
            "source": _wiki_url(sinner),
        }

    async def _identity_roster_context(self, query: str) -> dict | None:
        sinner = _sinner_from_query(query)
        if not sinner:
            return None
        pages = await asyncio.to_thread(self._identity_pages_for_sinner_sync, sinner)
        if not pages:
            return None
        try:
            rarity_by_page = await self._roster_template_values(pages, "rarity")
        except Exception:
            logger.exception("Không thể lấy rarity cho Identity roster: %s", sinner)
            rarity_by_page = {}
        entries = [
            {
                "name": page.title,
                "title": page.title,
                "url": _wiki_url(page.title),
                "rarity": rarity_by_page.get(page.pageid),
            }
            for page in pages
        ]
        return {
            "type": "identity_roster",
            "sinner": sinner,
            "count": len(entries),
            "entries": entries,
            "source": _wiki_url(sinner),
        }

    @staticmethod
    def _stat_from_rendered_html(rendered_html: str, icon_name: str) -> str | None:
        """Đọc stat đã được infobox tính theo level hiện hành."""
        match = re.search(
            rf'alt=["\']{re.escape(icon_name)}\.png["\'][\s\S]{{0,1000}}?'
            r'<span\b[^>]*>([\d,]+)</span>',
            str(rendered_html or ""),
            flags=re.IGNORECASE,
        )
        return match.group(1).replace(",", "") if match else None

    @staticmethod
    def _resistances_from_wikitext(wikitext: str) -> list[dict]:
        resistances: list[dict] = []
        for damage_type in ("slash", "pierce", "blunt"):
            match = re.search(
                rf"(?m)^\|{damage_type}\s*=\s*([^\n]+)", wikitext
            )
            if not match:
                continue
            raw_value = _plain_wikitext(match.group(1)).strip()
            normalized = raw_value.casefold().rstrip(".")
            label = "Ineffective" if normalized in {"ineff", "ineffective"} else raw_value
            resistances.append(
                {
                    "type": damage_type.title(),
                    "rating": label,
                    "multiplier": RESISTANCE_MULTIPLIERS.get(normalized, "?"),
                }
            )
        return resistances

    @staticmethod
    def _skill_from_template(slot: str, template: str) -> dict:
        params = _template_params(template)
        effects: list[str] = []
        if params.get("se"):
            effects.append(_plain_wikitext(params["se"]))
        coin_effects: list[dict] = []
        complex_coin_kind = ""
        complex_coin_count = 0
        complex_coin = str(params.get("complexcoin") or "").strip()
        if complex_coin:
            complex_parts = [part.strip() for part in complex_coin.split(",")]
            complex_coin_kind = complex_parts[0].casefold() if complex_parts else ""
            if len(complex_parts) >= 2:
                complex_coin_count = max(
                    0, int(re.sub(r"\D", "", complex_parts[1]) or 0)
                )
        regular_coin_count = max(
            0, int(re.sub(r"\D", "", params.get("coin", "0")) or 0)
        )
        effect_coin_count = max(
            (
                int(match.group(1))
                for key in params
                if (match := re.fullmatch(r"ce(\d+)", key))
            ),
            default=0,
        )
        # complexcoin=unbreakable,5 là cú pháp wiki dùng cho skill có 5 xu
        # đỏ. Nếu metadata thiếu, số ceN vẫn là fallback đáng tin hơn 0/1.
        coin_count = max(regular_coin_count, complex_coin_count, effect_coin_count)
        coin_kinds: list[str] = []
        for coin in range(1, max(coin_count, 1) + 1):
            raw_effect = params.get(f"ce{coin}", "")
            unbreakable = bool(
                complex_coin_kind == "unbreakable"
                or
                re.search(
                    r"\{\{StatusEffect\|Unbreakable Coin(?:\||\}\})",
                    raw_effect,
                    flags=re.IGNORECASE,
                )
            )
            coin_kinds.append("unbreakable" if unbreakable else "normal")
            effect = _plain_wikitext(raw_effect)
            if effect:
                coin_effects.append(
                    {
                        "coin": coin,
                        "effect": effect,
                        "kind": "unbreakable" if unbreakable else "normal",
                    }
                )
        label_map = {
            "skill1": "Skill 1", "skill1-2": "Skill 1-2",
            "skill2": "Skill 2", "skill2-2": "Skill 2-2",
            "skill3": "Skill 3", "skill3-2": "Skill 3-2",
            "skill3-3": "Skill 3-3", "defense": "Defense",
        }
        return {
            "slot": slot,
            "label": label_map.get(slot, slot.title()),
            "name": _plain_wikitext(params.get("name", "Unknown Skill")),
            "sin": params.get("sin", "None").strip().title(),
            "type": params.get("type", "").strip(),
            "base_power": params.get("spower", "").replace(" ", ""),
            "coin_power": params.get("cpower", "").strip(),
            "coins": coin_count,
            "coin_kinds": coin_kinds,
            "complex_coin": complex_coin or None,
            "amount": params.get("amt", "").strip(),
            "attack_weight": params.get("atkweight", "").strip(),
            "effects": [effect for effect in effects if effect],
            "coin_effects": coin_effects,
        }

    @staticmethod
    def _passives_from_wikitext(wikitext: str) -> list[dict]:
        start = wikitext.find("<!--Passive 0-->")
        end = wikitext.find("==Uptie Changes==", start)
        if start < 0:
            return []
        region = wikitext[start:end if end >= 0 else len(wikitext)]
        # Một số trang đặt passive Uptie cao nhất cạnh bản 2passiveN cũ.
        # Gộp theo danh tính passive và giữ bản có effect đầy đủ nhất.
        passive_by_identity: dict[tuple[str, str, str], dict] = {}
        for template in _extract_templates_from_region(region, "Passive"):
            positional, named = _split_template_arguments(template)
            if len(positional) < 2:
                continue
            entry = {
                "name": _plain_wikitext(positional[0]),
                "effect": _plain_wikitext(positional[1]),
                "sin": str(named.get("sin") or "None").title(),
                "requirement": _plain_wikitext(named.get("req", "")),
            }
            if not entry["name"]:
                continue
            identity = (
                _normalize_lookup(entry["name"]),
                entry["sin"].casefold(),
                _normalize_lookup(entry["requirement"]),
            )
            existing = passive_by_identity.get(identity)
            if existing is None or len(entry["effect"]) > len(existing["effect"]):
                passive_by_identity[identity] = entry
        return list(passive_by_identity.values())

    async def _identity_kit_context(
        self, query: str, *, requested_slot: str | None = None
    ) -> dict | None:
        page = await asyncio.to_thread(self._find_identity_page_sync, query)
        if not page:
            return None
        try:
            wikitext, rendered_html = await self._fetch_identity_source(page.pageid)
        except Exception:
            logger.exception("Không thể lấy wikitext kit: %s", page.title)
            return None
        asset = await self._page_asset(page, kind="identity")
        skills: list[dict] = []
        for slot in (
            "skill1", "skill1-2", "skill2", "skill2-2",
            "skill3", "skill3-2", "skill3-3", "defense",
        ):
            template = _extract_assigned_template(wikitext, slot)
            if template:
                skills.append(self._skill_from_template(slot, template))
        if requested_slot:
            skills = [skill for skill in skills if skill.get("slot") == requested_slot]
        if not skills:
            return None
        speed = re.search(r"(?m)^\|speed\s*=\s*([^\n]+)", wikitext)
        return {
            "type": "identity_kit",
            "title": page.title,
            "url": _wiki_url(page.title),
            "asset_url": str((asset or {}).get("asset_url") or ""),
            "asset_original_url": str((asset or {}).get("original_url") or ""),
            "asset_file": str((asset or {}).get("file_title") or ""),
            "hp": self._stat_from_rendered_html(rendered_html, "HP"),
            "speed": speed.group(1).strip() if speed else None,
            "defense_level": self._stat_from_rendered_html(rendered_html, "Defense"),
            "resistances": self._resistances_from_wikitext(wikitext),
            "skills": skills,
            "passives": [] if requested_slot else self._passives_from_wikitext(wikitext),
            "display_mode": "single_skill" if requested_slot else "full_kit",
            "requested_slot": requested_slot,
            "note": "Thông số lấy ở Uptie cao nhất đang ghi trong template wiki.",
        }

    async def _latest_release_context(self, query: str) -> dict | None:
        """Nguồn thời gian riêng cho câu hỏi Identity/E.G.O mới nhất."""
        history_title = "Extraction/Banner History"
        history: WikiPage | None = None
        try:
            # Trang này thay đổi thường xuyên, nên câu hỏi "mới nhất" luôn đọc
            # revision hiện tại thay vì đợi chu kỳ full sync 12 giờ.
            history = await self._fetch_page(history_title)
            await self._upsert_page(history)
        except Exception:
            logger.exception("Không thể làm mới Extraction/Banner History")
            history = await asyncio.to_thread(self._page_by_title_sync, history_title)
        if not history:
            return None

        banner = _parse_latest_banner(history.text)
        if not banner:
            return None
        query_text = query.casefold()
        wants_identity = "identity" in query_text or "identities" in query_text
        wants_ego = "ego" in query_text or "e.g.o" in query_text
        items = list(banner["items"])

        details: list[dict] = []
        for title in items[:4]:
            page = await asyncio.to_thread(self._page_by_title_sync, title)
            if not page:
                try:
                    page = await self._fetch_page(title)
                    await self._upsert_page(page)
                except Exception:
                    logger.warning("Không thể lấy trang release Limbus: %s", title, exc_info=True)
                    continue
            details.append({
                "title": page.title,
                "url": _wiki_url(page.title),
                "kind": self._classify_release(page.text),
                "release": self._extract_info_value(page.text, "Release"),
                "obtained": self._extract_info_value(page.text, "Obtained"),
                "content": page.text[:1800],
            })

        # Banner History trộn Identity/E.G.O trong cùng bảng. Khi người dùng hỏi
        # riêng một loại, metadata trang giúp model lọc; không đoán loại từ tên.
        return {
            "type": "latest_release_banner",
            "requested": {
                "identity": wants_identity,
                "ego": wants_ego,
            },
            "active": bool(banner["active"]),
            "as_of_kst": banner["as_of"].isoformat(),
            "starts_kst": banner["start"].isoformat(),
            "ends_kst": banner["end"].isoformat(),
            "items": items,
            "details": details,
            "history_source": history.url,
            "instruction": (
                "Đây là kết quả đối chiếu ngày hiện tại với Banner History. "
                "Nếu active=true, gọi đây là banner đang chạy; nếu false, chỉ gọi là banner gần nhất đã kết thúc."
            ),
        }

    @staticmethod
    def _extract_info_value(text: str, label: str) -> str | None:
        match = re.search(
            rf"(?im)^\|?\s*{re.escape(label)}\s*$\s*\n+(?:\s*\|\s*\n+)?\s*\|?\s*([^|\n]+)",
            text,
        )
        return match.group(1).strip() if match else None

    @staticmethod
    def _classify_release(text: str) -> str:
        lowered = text.casefold()
        if "cost (overclock)" in lowered or "threadspin changes" in lowered:
            return "E.G.O"
        if "uptie changes" in lowered or re.search(r"(?im)^\|?\s*rarity\s*$", text):
            return "Identity"
        return "Unknown"

    async def sync_all(self) -> None:
        if self.sync_lock.locked():
            return
        async with self.sync_lock:
            started = time.monotonic()
            catalog = await self._catalog()
            known = await asyncio.to_thread(self._known_revisions_sync)
            changed: list[tuple[int, str, int, str]] = []
            live_ids: set[int] = set()
            for item in catalog:
                pageid = int(item.get("pageid") or 0)
                revisions = item.get("revisions") or [{}]
                revid = int(revisions[0].get("revid") or item.get("lastrevid") or 0)
                timestamp = str(revisions[0].get("timestamp") or "")
                if pageid <= 0:
                    continue
                live_ids.add(pageid)
                title = str(item.get("title") or "")
                if _catalog_page_needs_refresh(title, known.get(pageid), revid):
                    changed.append((pageid, title, revid, timestamp))

            logger.info(
                "Limbus Wiki sync: %s bài, %s cần cập nhật", len(catalog), len(changed)
            )
            semaphore = asyncio.Semaphore(SYNC_CONCURRENCY)
            completed = 0
            failed = 0

            async def update_one(entry: tuple[int, str, int, str]) -> None:
                nonlocal completed, failed
                pageid, title, _revid, timestamp = entry
                async with semaphore:
                    try:
                        page = await self._fetch_page(
                            title, pageid=pageid, timestamp=timestamp
                        )
                        await self._upsert_page(page)
                        completed += 1
                        if completed % 50 == 0:
                            logger.info(
                                "Limbus Wiki sync: %s/%s trang thay đổi", completed, len(changed)
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        failed += 1
                        logger.warning("Không thể lập chỉ mục Limbus Wiki: %s", title, exc_info=True)

            await asyncio.gather(*(update_one(entry) for entry in changed))
            removed = await asyncio.to_thread(self._delete_missing_sync, live_ids)
            asset_completed, asset_failed = await self._sync_missing_assets()
            now = str(int(time.time()))
            await asyncio.to_thread(self._set_meta_sync, "last_sync", now)
            await asyncio.to_thread(self._set_meta_sync, "catalog_pages", str(len(catalog)))
            logger.info(
                "Limbus Wiki sync xong: cập nhật=%s, lỗi=%s, xóa=%s, "
                "asset=%s/%s, %.1fs",
                completed, failed, removed, asset_completed,
                asset_completed + asset_failed, time.monotonic() - started,
            )

    async def _sync_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.sync_all()
                await asyncio.sleep(SYNC_HOURS * 3600)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Limbus Wiki sync nền thất bại; vẫn dùng database gần nhất")
                await asyncio.sleep(min(1800, SYNC_HOURS * 3600))

    def _search_local_sync(self, query: str, limit: int) -> tuple[list[dict], str | None]:
        expanded_query = _expand_query(query)
        match = _fts_query(expanded_query)
        if not match:
            return [], None
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT c.pageid, c.title, c.section, c.content,
                       p.url, p.revid, p.timestamp,
                       bm25(wiki_chunks, 0.0, 8.0, 3.0, 1.0) AS score
                FROM wiki_chunks AS c
                JOIN wiki_pages AS p ON p.pageid = c.pageid
                WHERE wiki_chunks MATCH ?
                ORDER BY
                    CASE
                        WHEN lower(c.title) = lower(?) THEN 0
                        WHEN lower(c.title) LIKE '%' || lower(?) || '%' THEN 1
                        WHEN lower(?) LIKE '%' || lower(c.title) || '%' THEN 2
                        ELSE 3
                    END,
                    score,
                    CASE lower(c.section)
                        WHEN 'overview' THEN 0
                        WHEN 'skills' THEN 1
                        WHEN 'passives' THEN 2
                        WHEN 'uptie changes' THEN 3
                        ELSE 4
                    END
                LIMIT ?
                """,
                (
                    match,
                    query.strip(),
                    query.strip(),
                    expanded_query.strip(),
                    max(limit * 5, 20),
                ),
            ).fetchall()
            last = db.execute(
                "SELECT value FROM wiki_meta WHERE key='last_sync'"
            ).fetchone()
        results: list[dict] = []
        per_page: dict[int, int] = {}
        for row in rows:
            pageid = int(row["pageid"])
            if per_page.get(pageid, 0) >= 2:
                continue
            per_page[pageid] = per_page.get(pageid, 0) + 1
            results.append({
                "title": row["title"],
                "section": row["section"],
                "url": row["url"],
                "revision": int(row["revid"]),
                "updated": row["timestamp"],
                "content": str(row["content"])[:1800],
            })
            if len(results) >= limit:
                break
        return results, str(last[0]) if last else None

    async def _warm_query(self, query: str, limit: int = 3) -> int:
        expanded_query = _expand_query(query)
        alias_title = _alias_title(query)
        candidates: list[dict] = []
        seen: set[int] = set()

        # Tìm tiêu đề trước. MediaWiki full-text thường xếp bài chỉ nhắc tới status
        # cao hơn trang status chính nếu query có thêm các từ "effect/skill/passive".
        meaningful = [
            token
            for token in re.findall(r"[^\W_][\w.'’:-]+", expanded_query, flags=re.UNICODE)
            if token.casefold() not in {
                "skill", "skills", "passive", "passives", "status", "effect",
                "effects", "team", "build", "guide", "lore", "story", "mechanic",
                "cơ", "chế", "kỹ", "năng", "là", "gì", "dùng", "như", "thế", "nào",
            }
        ]
        title_phrase = alias_title or " ".join(meaningful[:10]).strip()
        if title_phrase:
            title_payload = await self._api_get(
                action="query",
                list="search",
                srsearch=f'intitle:"{title_phrase}"',
                srnamespace=0,
                srlimit=max(1, min(5, limit)),
            )
            for hit in title_payload.get("query", {}).get("search", []):
                pageid = int(hit.get("pageid") or 0)
                if pageid and pageid not in seen:
                    seen.add(pageid)
                    candidates.append(hit)

        payload = await self._api_get(
            action="query",
            list="search",
            srsearch=alias_title or expanded_query,
            srnamespace=0,
            srlimit=max(3, min(8, limit + 3)),
            srwhat="text",
        )
        for hit in payload.get("query", {}).get("search", []):
            pageid = int(hit.get("pageid") or 0)
            if pageid and pageid not in seen:
                seen.add(pageid)
                candidates.append(hit)

        warmed = 0
        for hit in candidates[: max(1, min(5, limit))]:
            title = str(hit.get("title") or "")
            if not title:
                continue
            try:
                page = await self._fetch_page(title, pageid=int(hit.get("pageid") or 0))
                await self._upsert_page(page)
                warmed += 1
            except Exception:
                logger.warning("Không thể cache trang Limbus Wiki: %s", title, exc_info=True)
        return warmed

    async def _asset_page_for_query(self, query: str) -> WikiPage | None:
        query = str(query or "").strip()
        if not query:
            return None
        page = await asyncio.to_thread(self._page_by_title_sync, query)
        if page:
            return page
        page = await asyncio.to_thread(self._find_identity_page_sync, query)
        if page:
            return page
        return await asyncio.to_thread(self._find_ego_page_sync, query)

    @limbusasset.command(name="status", description="Xem trạng thái kho artwork Limbus")
    async def asset_status(self, interaction: discord.Interaction) -> None:
        status = await asyncio.to_thread(self._asset_status_sync)
        last_sync = (
            f"<t:{status['last_sync']}:R>" if status["last_sync"] else "chưa có"
        )
        await interaction.response.send_message(
            "🖼️ **Limbus Asset Sync**\n"
            f"• Đã ghi nhận: `{status['total']}` trang\n"
            f"• Có artwork dùng được: `{status['with_image']}` trang\n"
            f"• Lần cập nhật gần nhất: {last_sync}\n"
            "• Asset được tải theo nhu cầu và tự làm mới khi revision wiki đổi.",
            ephemeral=True,
        )

    @limbusasset.command(name="preview", description="Xem artwork wiki của Identity/E.G.O")
    @app_commands.describe(name="Tên đầy đủ hoặc alias Identity/E.G.O")
    async def asset_preview(
        self, interaction: discord.Interaction, name: str
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        page = await self._asset_page_for_query(name)
        if not page:
            return await interaction.followup.send(
                "❌ Không tìm thấy đúng trang Identity/E.G.O này trong dữ liệu đã đồng bộ.",
                ephemeral=True,
            )
        try:
            asset = await asyncio.wait_for(
                self._page_asset(page, force=True), timeout=30
            )
        except asyncio.TimeoutError:
            return await interaction.followup.send(
                "⏱️ Wiki/CDN phản hồi quá 30 giây. Cache cũ không bị mất; hãy thử lại sau.",
                ephemeral=True,
            )
        if not asset or not asset.get("asset_url"):
            return await interaction.followup.send(
                f"ℹ️ Trang **{page.title}** hiện không có ảnh đại diện đọc được qua wiki API.",
                ephemeral=True,
            )
        embed = discord.Embed(
            title=page.title,
            url=page.url,
            description=(
                f"`{asset.get('file_title') or 'Wiki asset'}`\n"
                "Ảnh này sẽ tự xuất hiện trong embed kit tương ứng."
            ),
            color=0x2B2D31,
        )
        embed.set_image(url=str(asset["asset_url"]))
        embed.set_footer(text="Nguồn: Limbus Company Wiki (wiki.gg) • CC BY-SA 4.0")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @limbusasset.command(
        name="sync", description="[Chủ bot] Buộc làm mới artwork của một Identity/E.G.O"
    )
    @app_commands.describe(name="Tên đầy đủ hoặc alias Identity/E.G.O")
    async def asset_sync(self, interaction: discord.Interaction, name: str) -> None:
        if not await self.bot.is_owner(interaction.user):
            return await interaction.response.send_message(
                "❌ Chỉ chủ bot mới được buộc đồng bộ Limbus asset.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        page = await self._asset_page_for_query(name)
        if not page:
            return await interaction.followup.send(
                "❌ Không tìm thấy đúng trang Identity/E.G.O này trong dữ liệu đã đồng bộ.",
                ephemeral=True,
            )
        try:
            asset = await asyncio.wait_for(
                self._page_asset(page, force=True), timeout=30
            )
        except asyncio.TimeoutError:
            return await interaction.followup.send(
                "⏱️ Wiki/CDN phản hồi quá 30 giây. Cache cũ không bị mất; hãy thử lại sau.",
                ephemeral=True,
            )
        if not asset or not asset.get("asset_url"):
            return await interaction.followup.send(
                f"ℹ️ Đã kiểm tra **{page.title}**, nhưng wiki chưa trả artwork dùng được.",
                ephemeral=True,
            )
        await interaction.followup.send(
            f"✅ Đã làm mới artwork của **{page.title}**. Dùng "
            f"`/limbusasset preview name:{page.title}` để xem.",
            ephemeral=True,
        )

    async def search(self, query: str, limit: int = 6, *, context: str = "") -> dict:
        query = str(query or "").strip()[:300]
        if not query:
            return {"status": "error", "message": "Thiếu từ khóa tra cứu.", "results": []}
        latest_release = None
        roster = None
        identity_kit = None
        identity_roster = None
        ego_detail = None
        ego_roster = None
        temporal_query = f"{query} {str(context or '')[:500]}".strip()
        requested_slot = _requested_identity_skill_slot(temporal_query)
        if _is_latest_release_query(temporal_query):
            latest_release = await self._latest_release_context(temporal_query)
        if _is_nursefather_roster_query(temporal_query):
            roster = await self._nursefather_roster()
        if _is_identity_roster_query(temporal_query):
            identity_roster = await self._identity_roster_context(temporal_query)
        elif _is_ego_roster_query(temporal_query):
            ego_roster = await self._ego_roster_context(temporal_query)
        elif _is_ego_detail_query(temporal_query):
            ego_detail = await self._ego_detail_context(temporal_query)
        elif _is_identity_kit_query(temporal_query):
            identity_kit = await self._identity_kit_context(temporal_query)
        elif requested_slot:
            identity_kit = await self._identity_kit_context(
                temporal_query, requested_slot=requested_slot
            )
        if identity_kit or identity_roster or ego_detail or ego_roster:
            return {
                "status": "ok",
                "query": query,
                "last_full_sync_unix": None,
                "license": "CC BY-SA 4.0",
                "source": "Limbus Company Wiki (wiki.gg)",
                "latest_release": latest_release,
                "nursefather_roster": roster,
                "identity_kit": identity_kit,
                "identity_roster": identity_roster,
                "ego_detail": ego_detail,
                "ego_roster": ego_roster,
                "results": [],
            }
        results, last_sync = await asyncio.to_thread(
            self._search_local_sync, query, max(1, min(8, limit))
        )
        # Khi lần đồng bộ toàn bộ đầu tiên chưa xong, vài chunk cache rời rạc có
        # thể khớp từ chung (vd. "status") nhưng không phải đúng trang cần hỏi.
        # Luôn warm theo tiêu đề/query cho tới khi có mốc full sync.
        if not last_sync or len(results) < 2:
            try:
                await self._warm_query(query)
                results, last_sync = await asyncio.to_thread(
                    self._search_local_sync, query, max(1, min(8, limit))
                )
            except Exception:
                logger.exception("Tra trực tiếp Limbus Wiki thất bại: %s", query)
        return {
            "status": "ok" if results else "empty",
            "query": query,
            "last_full_sync_unix": int(last_sync) if last_sync else None,
            "license": "CC BY-SA 4.0",
            "source": "Limbus Company Wiki (wiki.gg)",
            "latest_release": latest_release,
            "nursefather_roster": roster,
            "identity_kit": identity_kit,
            "identity_roster": identity_roster,
            "ego_detail": ego_detail,
            "ego_roster": ego_roster,
            "results": results,
        }


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LimbusWiki(bot))
