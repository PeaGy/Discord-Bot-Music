"""Limbus Company Standard Extraction renderer and optional economy game.

The feature deliberately reads the already-synced wiki database instead of
scraping the live website for every pull. It stays a free simulator in servers
where Peto Economy is disabled; enabled servers spend Peto Points and persist
their collection in the shared economy database.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import random
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import (
    Image,
    ImageChops,
    ImageDraw,
    ImageFilter,
    ImageOps,
    UnidentifiedImageError,
)

from economy_store import (
    AlreadyOwned,
    EconomyAccount,
    EconomyDisabled,
    InsufficientExtractionPoints,
    InsufficientPoints,
    get_economy_store,
)
from features._blue_archive_gacha import (
    GAME_ID as BLUE_ARCHIVE_GAME_ID,
    BlueArchiveBanner,
    BlueArchiveGachaService,
    BlueArchivePull,
    BlueArchiveStudent,
    blue_archive_rates_embed,
    mark_new_blue_archive_pulls,
    pull_blue_archive,
)
from features._brown_dust_2_gacha import (
    GAME_ID as BROWN_DUST_2_GAME_ID,
    KIND_STAR3 as BD2_KIND_STAR3,
    KIND_STAR4 as BD2_KIND_STAR4,
    KIND_STAR5 as BD2_KIND_STAR5,
    BrownDust2GachaService,
    BrownDust2Pity,
    BrownDust2Pool,
    BrownDust2Pull,
    brown_dust_2_rates_embed,
    mark_new_brown_dust_2_pulls,
    pull_brown_dust_2,
)
from features._fgo_gacha import (
    GAME_ID as FGO_GAME_ID,
    KIND_CE_3,
    KIND_CE_4,
    KIND_CE_5,
    KIND_SERVANT_3,
    KIND_SERVANT_4,
    KIND_SERVANT_5,
    FGOGachaService,
    FGOPool,
    FGOPull,
    fgo_rates_embed,
    mark_new_fgo_pulls,
    pull_fgo,
)


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        logger.warning("%s không hợp lệ; dùng mặc định %s", name, default)
        return default


WIKI_ROOT = "https://limbuscompany.wiki.gg/wiki/"
DB_PATH = Path(os.getenv("LIMBUS_WIKI_DB", "limbus_knowledge.db")).resolve()
POOL_REFRESH_SECONDS = _env_int("LIMBUS_GACHA_POOL_REFRESH_SECONDS", 900, 30)
VIEW_TIMEOUT_SECONDS = _env_int("LIMBUS_GACHA_VIEW_TIMEOUT_SECONDS", 180, 30)
IMAGE_MAX_BYTES = _env_int("LIMBUS_GACHA_IMAGE_MAX_MIB", 8, 1) * 1024 * 1024
IMAGE_TIMEOUT_SECONDS = _env_int("LIMBUS_GACHA_IMAGE_TIMEOUT_SECONDS", 45, 15)
ART_CACHE_DIR = Path(
    os.getenv("LIMBUS_GACHA_ART_CACHE_DIR", "limbus_gacha_art_cache")
).resolve()
GACHA_UI_DIR = Path(__file__).resolve().parent.parent / "assets" / "limbus_gacha"
GACHA_CANVAS_SIZE = (1280, 720)
GACHA_COLUMN_CENTERS = (180, 410, 640, 870, 1100)
GACHA_ROW_CENTERS = (250, 470)
IDENTITY_VISIBLE_SIZE = (225, 150)
EGO_VISIBLE_SIZE = (198, 198)
FRAME_ALPHA_THRESHOLD = 12
FRAME_RENDER_PADDING = 12
IDENTITY_ART_MASK_EXPANSION = 21
# Wiki profile artwork is square while the in-game result window is wide. A
# straight cover crop cuts away too much of the character and turns every pull
# into a face close-up. Keep a softer cover layer behind a slightly zoomed-out
# foreground so the slot stays filled while more of the original art remains.
IDENTITY_ARTWORK_FOREGROUND_SCALE = 0.78
IDENTITY_ARTWORK_BACKGROUND_BLUR = 3.0

KIND_EGO = "ego"
KIND_ID3 = "id3"
KIND_ID2 = "id2"
KIND_ID1 = "id1"

# Standard Extraction rates. Identity and E.G.O duplicates are both allowed so
# the simulator keeps every result in collection and increments its copy count.
STANDARD_RATES: tuple[tuple[str, float], ...] = (
    (KIND_EGO, 1.3),
    (KIND_ID3, 2.9),
    (KIND_ID2, 12.8),
    (KIND_ID1, 83.0),
)
TENTH_PULL_RATES: tuple[tuple[str, float], ...] = (
    (KIND_EGO, 1.3),
    (KIND_ID3, 2.9),
    (KIND_ID2, 95.8),
)
GACHA_POINT_COST = {1: 130, 10: 1300, 11: 1300}
EXTRACTION_EXCHANGE_COST = 200

RARITY_EMOJI = {
    KIND_ID1: "<:IDNumber1:1537226566507962449>",
    KIND_ID2: "<:IDNumber2:1537226521830367292>",
    KIND_ID3: "<:IDNumber3:1537226464364339220>",
    KIND_EGO: "✨",
}
RARITY_TEXT = {
    KIND_ID1: "1★ Identity",
    KIND_ID2: "2★ Identity",
    KIND_ID3: "3★ Identity",
    KIND_EGO: "E.G.O",
}
RARITY_COLOR = {
    KIND_ID1: (112, 119, 126),
    KIND_ID2: (215, 54, 62),
    KIND_ID3: (241, 190, 46),
    # Discord chỉ cho embed một màu accent; cam đỏ là màu trung gian của
    # khung vàng–đỏ dùng cho E.G.O trong collage.
    KIND_EGO: (231, 105, 38),
}
EGO_FRAME_GOLD = (241, 190, 46)
EGO_FRAME_RED = (202, 45, 50)
FRAME_ASSET_NAMES = {
    KIND_ID1: "frame_id1.png",
    KIND_ID2: "frame_id2.png",
    KIND_ID3: "frame_id3.png",
    KIND_EGO: "frame_ego.png",
}
FRAME_GLOW_COLORS = {
    KIND_ID1: (174, 105, 22),
    KIND_ID2: (235, 33, 29),
    KIND_ID3: (255, 202, 42),
    KIND_EGO: (224, 229, 224),
}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _roster_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = value.replace("[", "").replace("]", "")
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE)


def parse_extraction_list(text: str) -> dict[str, list[str]]:
    """Parse the four pools from wiki.gg's rendered Extraction List page."""
    pools = {KIND_ID3: [], KIND_ID2: [], KIND_ID1: [], KIND_EGO: []}
    current: str | None = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        folded = line.casefold()
        if "identities have an extraction rate of 2.9%" in folded:
            current = KIND_ID3
            continue
        if "identities have an extraction rate of 12.8%" in folded:
            current = KIND_ID2
            continue
        if (
            "identities have an extraction rate of 83%" in folded
            or "identities have an extraction rate of 83.0%" in folded
        ):
            current = KIND_ID1
            continue
        if line == "|":
            if current == KIND_ID1 and pools[KIND_ID1]:
                current = KIND_EGO
            continue
        if not current or not line or line == "IdentitiesE.G.O":
            continue
        if "extraction rate" in folded:
            continue
        if line not in pools[current]:
            pools[current].append(line)
    return pools


@dataclass(frozen=True, slots=True)
class GachaEntry:
    name: str
    kind: str
    url: str
    image_url: str = ""


@dataclass(frozen=True, slots=True)
class GachaPool:
    by_kind: Mapping[str, tuple[GachaEntry, ...]]

    def entries(self, kind: str) -> tuple[GachaEntry, ...]:
        return self.by_kind.get(kind, ())


@dataclass(slots=True)
class GachaPayload:
    embed: discord.Embed
    file: discord.File | None
    pulls: tuple[GachaEntry, ...]


def _wiki_url(name: str) -> str:
    return WIKI_ROOT + quote(name.replace(" ", "_"), safe="()'.,:-")


def _fallback_asset_url(name: str, kind: str) -> str:
    """MediaWiki redirect that also works before wiki_assets is refreshed."""
    clean_name = re.sub(r"\s+", " ", str(name).replace(":", " ")).strip()
    suffix = " Icon.png" if kind == KIND_EGO else " Profile.png"
    filename = quote((clean_name + suffix).replace(" ", "_"), safe="()'.,-")
    return f"{WIKI_ROOT}Special:Redirect/file/{filename}"


def _info_block_value(text: str, label: str) -> str:
    """Read one value from the rendered wiki infobox."""
    match = re.search(
        rf"(?im)^\|?\s*{re.escape(label)}\s*$\s*\n+"
        rf"(?:\s*\|\s*\n+)?\s*\|?\s*([^|\n]+)",
        str(text or ""),
    )
    return match.group(1).strip() if match else ""


def _is_walpurgis_page(text: str) -> bool:
    return "walpurgis" in _info_block_value(text, "Season").casefold()


def _is_identity_page(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).casefold()
    return "skill1skill2skill3defense" in compact


def _is_extraction_ego_page(text: str) -> bool:
    folded = str(text or "").casefold()
    return (
        "risk level" in folded
        and _info_block_value(text, "Obtained").casefold() == "extraction"
    )


def _listed_three_star_keys(text: str) -> set[str]:
    """Return exact roster keys from the 3-star section of the rarity list."""
    match = re.search(
        r"There\s+are\s*\d+\s*Identities\s+that\s+have\s+a\s+3★\s+rarity\.?",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return set()
    return {
        _roster_key(line.strip().lstrip("|").strip())
        for line in str(text)[match.end() :].splitlines()
        if line.strip().lstrip("|").strip()
    }


def _listed_ego_keys(text: str) -> set[str]:
    """Return possible exact E.G.O page keys from the wiki data table."""
    return {
        _roster_key(line.strip().lstrip("|").strip())
        for line in str(text or "").splitlines()
        if line.strip().lstrip("|").strip()
    }


def _prepare_art_cache_dir(path: Path) -> Path | None:
    """Create the optional disk cache without ever preventing bot startup."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path if path.is_dir() else None
    except OSError as error:
        logger.warning(
            "Không tạo được Limbus Gacha art cache tại %s; tiếp tục không dùng cache đĩa: %s",
            path,
            error,
        )
        return None


def _image_is_decodable(data: bytes) -> bool:
    if not data:
        return False
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
        return True
    except (OSError, UnidentifiedImageError, ValueError):
        return False


async def _read_limited_image_response(
    response: aiohttp.ClientResponse, limit: int
) -> bytes | None:
    """Read the complete body while refusing payloads larger than ``limit``."""
    if response.content_length is not None and response.content_length > limit:
        return None
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def load_gacha_pool_sync(db_path: Path = DB_PATH) -> GachaPool:
    if not db_path.is_file():
        raise RuntimeError(f"Không tìm thấy database Limbus: {db_path}")
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        page = connection.execute(
            "SELECT text FROM wiki_pages WHERE title = ? COLLATE NOCASE",
            ("Extraction/Extraction List",),
        ).fetchone()
        if not page or not str(page["text"] or "").strip():
            raise RuntimeError(
                "Database chưa có trang Extraction/Extraction List; hãy chờ Limbus Wiki sync xong."
            )
        names_by_kind = parse_extraction_list(str(page["text"]))
        asset_table = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'wiki_assets'
            """
        ).fetchone()
        if asset_table:
            rows = connection.execute(
                """
                SELECT p.title, p.url,
                       COALESCE(a.thumbnail_url, a.original_url, '') AS image_url
                FROM wiki_pages AS p
                LEFT JOIN wiki_assets AS a ON a.pageid = p.pageid
                """
            ).fetchall()
        else:
            # Database được đồng bộ từ phiên bản cũ vẫn có đủ roster để quay.
            # Artwork dùng URL MediaWiki dự phòng cho tới khi LimbusWiki tạo bảng
            # wiki_assets trong lần khởi động kế tiếp.
            rows = connection.execute(
                """
                SELECT p.title, p.url, '' AS image_url
                FROM wiki_pages AS p
                """
            ).fetchall()
    finally:
        connection.close()

    pages = {_roster_key(row["title"]): row for row in rows}
    result: dict[str, tuple[GachaEntry, ...]] = {}
    for kind, names in names_by_kind.items():
        entries: list[GachaEntry] = []
        for name in names:
            row = pages.get(_roster_key(name))
            title = str(row["title"]) if row else name
            image_url = str(row["image_url"] or "") if row else ""
            if not image_url:
                image_url = _fallback_asset_url(title, kind)
            entries.append(
                GachaEntry(
                    name=title,
                    kind=kind,
                    url=str(row["url"] or _wiki_url(title)) if row else _wiki_url(title),
                    image_url=image_url,
                )
            )
        result[kind] = tuple(entries)

    minimums = {KIND_ID3: 20, KIND_ID2: 20, KIND_ID1: 12, KIND_EGO: 10}
    invalid = [
        f"{kind}={len(result.get(kind, ()))}/{minimum}"
        for kind, minimum in minimums.items()
        if len(result.get(kind, ())) < minimum
    ]
    if invalid:
        raise RuntimeError("Pool Extraction chưa đầy đủ: " + ", ".join(invalid))
    return GachaPool(result)


def load_exchange_catalog_sync(db_path: Path = DB_PATH) -> GachaPool:
    """Load exchangeable 3-star Identities and E.G.O outside Standard pool.

    Seasonal and Event entries are intentionally included. Walpurgis entries are
    intentionally excluded because they can only be dispensed during Walpurgis.
    """
    if not db_path.is_file():
        raise RuntimeError(f"Không tìm thấy database Limbus: {db_path}")
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        rarity_page = connection.execute(
            "SELECT text FROM wiki_pages WHERE title = ? COLLATE NOCASE",
            ("List of Identities/Rarity",),
        ).fetchone()
        if not rarity_page or not str(rarity_page["text"] or "").strip():
            raise RuntimeError(
                "Database chưa có trang List of Identities/Rarity; "
                "hãy chờ Limbus Wiki sync xong."
            )
        three_star_keys = _listed_three_star_keys(str(rarity_page["text"]))
        if not three_star_keys:
            raise RuntimeError("Không đọc được danh sách Identity 3★ từ Limbus Wiki.")
        ego_data_page = connection.execute(
            "SELECT text FROM wiki_pages WHERE title = ? COLLATE NOCASE",
            ("List of E.G.O/Data",),
        ).fetchone()
        ego_keys = (
            _listed_ego_keys(str(ego_data_page["text"] or ""))
            if ego_data_page
            else set()
        )

        has_assets = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'wiki_assets'"
        ).fetchone()
        if has_assets:
            rows = connection.execute(
                """
                SELECT p.title, p.url, p.text,
                       COALESCE(a.thumbnail_url, a.original_url, '') AS image_url
                FROM wiki_pages AS p
                LEFT JOIN wiki_assets AS a ON a.pageid = p.pageid
                """
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT title, url, text, '' AS image_url FROM wiki_pages"
            ).fetchall()
    finally:
        connection.close()

    result: dict[str, list[GachaEntry]] = {KIND_ID3: [], KIND_EGO: []}
    for row in rows:
        title = str(row["title"] or "").strip()
        text = str(row["text"] or "")
        if not title or "/" in title or _is_walpurgis_page(text):
            continue
        key = _roster_key(title)
        if key in three_star_keys and _is_identity_page(text):
            kind = KIND_ID3
        elif key in ego_keys and "risk level" in text.casefold():
            obtained = _info_block_value(text, "Obtained").casefold()
            season = _info_block_value(text, "Season").casefold()
            if obtained == "base e.g.o" or season in {"", "n/a"}:
                continue
            kind = KIND_EGO
        elif not ego_keys and _is_extraction_ego_page(text):
            # Older databases may predate List of E.G.O/Data. Keep the known
            # Extraction subset available until the next background wiki sync.
            kind = KIND_EGO
        else:
            continue
        image_url = str(row["image_url"] or "") or _fallback_asset_url(title, kind)
        result[kind].append(
            GachaEntry(
                name=title,
                kind=kind,
                url=str(row["url"] or _wiki_url(title)),
                image_url=image_url,
            )
        )

    catalog = {
        kind: tuple(sorted(entries, key=lambda entry: entry.name.casefold()))
        for kind, entries in result.items()
    }
    if not catalog[KIND_ID3] or not catalog[KIND_EGO]:
        raise RuntimeError(
            "Danh mục đổi chưa đầy đủ: "
            f"Identity 3★={len(catalog[KIND_ID3])}, E.G.O={len(catalog[KIND_EGO])}"
        )
    return GachaPool(catalog)


def roll_kind(
    rng: random.Random,
    rates: Sequence[tuple[str, float]] = STANDARD_RATES,
) -> str:
    point = rng.random() * sum(weight for _, weight in rates)
    cumulative = 0.0
    for kind, weight in rates:
        cumulative += weight
        if point < cumulative:
            return kind
    return rates[-1][0]


def pull_entries(
    pool: GachaPool,
    count: int,
    rng: random.Random | None = None,
) -> tuple[GachaEntry, ...]:
    if count not in {1, 10}:
        raise ValueError("Chỉ hỗ trợ quay 1 hoặc 10 lần.")
    rng = rng or random.SystemRandom()
    results: list[GachaEntry] = []
    for index in range(count):
        rates = TENTH_PULL_RATES if count == 10 and index == 9 else STANDARD_RATES
        kind = roll_kind(rng, rates)
        candidates = pool.entries(kind)
        if not candidates:
            raise RuntimeError(f"Pool {kind} đang trống.")
        result = rng.choice(candidates)
        results.append(result)
    return tuple(results)


def _open_gacha_ui_asset(filename: str) -> Image.Image:
    path = GACHA_UI_DIR / filename
    if not path.is_file():
        raise RuntimeError(f"Thiếu Limbus Gacha UI asset: {path}")
    try:
        with Image.open(path) as source:
            source.load()
            return source.convert("RGBA")
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise RuntimeError(f"Không đọc được Limbus Gacha UI asset: {path}") from error


def _frame_interior_mask(frame: Image.Image) -> Image.Image:
    """Return the transparent component enclosed by a frame.

    The source textures include transparent padding outside the frame as well as
    a transparent window in its centre. Flood-filling from the centre isolates
    the window without letting character artwork leak around the outer glow.
    """
    alpha = frame.getchannel("A")
    flood_map = alpha.point(
        lambda value: 255 if value > FRAME_ALPHA_THRESHOLD else 0
    )
    centre = (frame.width // 2, frame.height // 2)
    if flood_map.getpixel(centre) != 0:
        raise RuntimeError("Limbus Gacha frame không có vùng ảnh trong suốt ở giữa.")
    ImageDraw.floodfill(flood_map, centre, 128, thresh=0)
    return flood_map.point(lambda value: 255 if value == 128 else 0)


def _visible_alpha_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    alpha = image.getchannel("A")
    visible = alpha.point(
        lambda value: 255 if value > FRAME_ALPHA_THRESHOLD else 0
    )
    bounds = visible.getbbox()
    if not bounds:
        raise RuntimeError("Limbus Gacha frame không có pixel hữu hình.")
    return bounds


def _normalize_frame_and_mask(
    frame: Image.Image,
    interior_mask: Image.Image,
    visible_size: tuple[int, int],
) -> tuple[Image.Image, Image.Image]:
    """Scale different source canvases by their visible alpha bounds.

    The four game textures use unrelated canvas sizes and transparent padding.
    Scaling the raw canvases makes otherwise identical result slots look uneven.
    This normalizes the visible frame while preserving a small amount of its
    built-in outer glow.
    """
    left, top, right, bottom = _visible_alpha_bbox(frame)
    scale_x = visible_size[0] / (right - left)
    scale_y = visible_size[1] / (bottom - top)
    resized_size = (
        max(1, round(frame.width * scale_x)),
        max(1, round(frame.height * scale_y)),
    )
    resized_frame = frame.resize(resized_size, Image.Resampling.LANCZOS)
    resized_mask = interior_mask.resize(resized_size, Image.Resampling.LANCZOS)

    visible_left, visible_top, visible_right, visible_bottom = _visible_alpha_bbox(
        resized_frame
    )
    visible_centre_x = (visible_left + visible_right) / 2
    visible_centre_y = (visible_top + visible_bottom) / 2
    output_size = (
        visible_size[0] + FRAME_RENDER_PADDING * 2,
        visible_size[1] + FRAME_RENDER_PADDING * 2,
    )
    crop_left = round(visible_centre_x - output_size[0] / 2)
    crop_top = round(visible_centre_y - output_size[1] / 2)
    crop_box = (
        crop_left,
        crop_top,
        crop_left + output_size[0],
        crop_top + output_size[1],
    )
    return resized_frame.crop(crop_box), resized_mask.crop(crop_box)


@lru_cache(maxsize=1)
def _prepared_gacha_background() -> Image.Image:
    background = _open_gacha_ui_asset("background.png")
    return ImageOps.fit(
        background,
        GACHA_CANVAS_SIZE,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


@lru_cache(maxsize=4)
def _prepared_gacha_frame(kind: str) -> tuple[Image.Image, Image.Image]:
    try:
        filename = FRAME_ASSET_NAMES[kind]
    except KeyError as error:
        raise ValueError(f"Loại kết quả gacha không hợp lệ: {kind}") from error
    frame = _open_gacha_ui_asset(filename)
    interior_mask = _frame_interior_mask(frame)
    visible_size = EGO_VISIBLE_SIZE if kind == KIND_EGO else IDENTITY_VISIBLE_SIZE
    normalized_frame, normalized_mask = _normalize_frame_and_mask(
        frame,
        interior_mask,
        visible_size,
    )
    if kind != KIND_EGO:
        # Broken-glass pieces are part of the foreground texture. The original
        # centre flood-fill stops at those pieces, leaving a window that is too
        # small after the frame is enlarged. Let artwork extend beneath the
        # glass and chains; the frame is composited afterwards and masks them.
        normalized_mask = normalized_mask.filter(
            ImageFilter.MaxFilter(IDENTITY_ART_MASK_EXPANSION)
        )
    return normalized_frame, normalized_mask


def _render_artwork(
    raw: bytes,
    frame_size: tuple[int, int],
    interior_mask: Image.Image,
    *,
    ego: bool,
) -> Image.Image | None:
    if not raw:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            artwork = source.convert("RGBA")
    except (OSError, UnidentifiedImageError, ValueError):
        return None

    bounds = interior_mask.getbbox()
    if not bounds:
        return None
    left, top, right, bottom = bounds
    window_size = (right - left, bottom - top)
    centering = (0.5, 0.5 if ego else 0.42)
    fitted = ImageOps.fit(
        artwork,
        window_size,
        method=Image.Resampling.LANCZOS,
        # Identity profile art usually places the face above centre. E.G.O icons
        # are already square and should stay geometrically centred.
        centering=centering,
    )
    if not ego:
        # The wiki only exposes square profile thumbnails for most Identities,
        # unlike the wider result artwork used by the game. Use the normal cover
        # crop as a blurred edge fill, then place a smaller copy over it. This
        # reveals more shoulders/background without leaving empty bars inside the
        # broken-glass frame.
        fitted = fitted.filter(
            ImageFilter.GaussianBlur(IDENTITY_ARTWORK_BACKGROUND_BLUR)
        )
        cover_scale = max(
            window_size[0] / artwork.width,
            window_size[1] / artwork.height,
        )
        foreground_size = (
            max(
                1,
                round(
                    artwork.width
                    * cover_scale
                    * IDENTITY_ARTWORK_FOREGROUND_SCALE
                ),
            ),
            max(
                1,
                round(
                    artwork.height
                    * cover_scale
                    * IDENTITY_ARTWORK_FOREGROUND_SCALE
                ),
            ),
        )
        foreground = artwork.resize(foreground_size, Image.Resampling.LANCZOS)
        foreground_x = round((window_size[0] - foreground.width) * centering[0])
        foreground_y = round((window_size[1] - foreground.height) * centering[1])
        fitted.alpha_composite(foreground, (foreground_x, foreground_y))
    layer = Image.new("RGBA", frame_size, (0, 0, 0, 0))
    layer.alpha_composite(fitted, (left, top))
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), interior_mask))
    return layer


def _render_frame_glow(frame: Image.Image, kind: str, padding: int = 18) -> Image.Image:
    expanded_size = (frame.width + padding * 2, frame.height + padding * 2)
    alpha = Image.new("L", expanded_size, 0)
    alpha.paste(frame.getchannel("A"), (padding, padding))
    alpha = alpha.filter(ImageFilter.GaussianBlur(12))
    # Keep the original in-game frame glow subtle; the background already
    # contains bright particles and should remain the visual focus.
    alpha = alpha.point(lambda value: min(120, int(value * 0.72)))
    glow = Image.new("RGBA", expanded_size, FRAME_GLOW_COLORS[kind] + (0,))
    glow.putalpha(alpha)
    return glow


def render_gacha_collage(
    pulls: Sequence[GachaEntry],
    image_data: Mapping[str, bytes],
) -> bytes:
    """Render ten pulls as a Limbus-style two-row result screen."""
    if len(pulls) != 10:
        raise ValueError("Collage cần đúng 10 kết quả.")
    canvas = _prepared_gacha_background().copy()
    for index, entry in enumerate(pulls):
        column, row = index % 5, index // 5
        frame, interior_mask = _prepared_gacha_frame(entry.kind)
        frame = frame.copy()
        interior_mask = interior_mask.copy()
        x = GACHA_COLUMN_CENTERS[column] - frame.width // 2
        y = GACHA_ROW_CENTERS[row] - frame.height // 2

        glow = _render_frame_glow(frame, entry.kind)
        glow_padding_x = (glow.width - frame.width) // 2
        glow_padding_y = (glow.height - frame.height) // 2
        canvas.alpha_composite(glow, (x - glow_padding_x, y - glow_padding_y))

        artwork = _render_artwork(
            image_data.get(entry.image_url, b""),
            frame.size,
            interior_mask,
            ego=entry.kind == KIND_EGO,
        )
        if artwork is not None:
            canvas.alpha_composite(artwork, (x, y))
        canvas.alpha_composite(frame, (x, y))

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def _escaped_link(entry: GachaEntry) -> str:
    name = discord.utils.escape_markdown(entry.name)
    return f"[{name}](<{entry.url}>)"


def build_rates_embed(pool: GachaPool | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="📊 Standard Extraction — Tỷ lệ",
        description=(
            "**Lượt thường**\n"
            "✨ E.G.O — `1.3%`\n"
            f"{RARITY_EMOJI[KIND_ID3]} 3★ Identity — `2.9%`\n"
            f"{RARITY_EMOJI[KIND_ID2]} 2★ Identity — `12.8%`\n"
            f"{RARITY_EMOJI[KIND_ID1]} 1★ Identity — `83.0%`\n\n"
            "**Lượt thứ 10**\n"
            "✨ E.G.O — `1.3%`\n"
            f"{RARITY_EMOJI[KIND_ID3]} 3★ Identity — `2.9%`\n"
            f"{RARITY_EMOJI[KIND_ID2]} 2★ Identity — `95.8%`"
        ),
        color=0xA68B5B,
    )
    if pool:
        embed.add_field(
            name="Pool đang mô phỏng",
            value=(
                f"3★ `{len(pool.entries(KIND_ID3))}` • "
                f"2★ `{len(pool.entries(KIND_ID2))}` • "
                f"1★ `{len(pool.entries(KIND_ID1))}` • "
                f"E.G.O `{len(pool.entries(KIND_EGO))}`"
            ),
            inline=False,
        )
    embed.set_footer(
        text="Lượt 10 bảo đảm 2★ trở lên • Economy bật thì kết quả được lưu"
    )
    return embed


class LimbusGachaView(discord.ui.View):
    def __init__(self, cog: "LimbusGacha", owner_id: int):
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.cog = cog
        self.owner_id = int(owner_id)
        self.message: discord.Message | None = None
        self.roll_lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "🎰 Panel này thuộc lượt quay của người khác. Dùng `/gacha` để mở lượt riêng nhé.",
            ephemeral=True,
        )
        return False

    async def _reroll(self, interaction: discord.Interaction, count: int) -> None:
        if self.roll_lock.locked():
            return await interaction.response.send_message(
                "⏳ Lượt quay trước vẫn đang được dựng ảnh.", ephemeral=True
            )
        await interaction.response.defer()
        async with self.roll_lock:
            try:
                if interaction.guild_id is None:
                    raise EconomyDisabled("Gacha economy chỉ dùng trong server.")
                payload = await self.cog.perform_gacha(
                    interaction.guild_id,
                    interaction.user.id,
                    count,
                    source_id=f"interaction:{interaction.id}",
                )
                attachments: list[discord.File] = [payload.file] if payload.file else []
                await interaction.edit_original_response(
                    embed=payload.embed,
                    attachments=attachments,
                    view=self,
                )
            except (InsufficientPoints, EconomyDisabled) as error:
                await interaction.followup.send(f"❌ {error}", ephemeral=True)
            except Exception as error:
                logger.exception("Không thể quay lại Limbus gacha")
                await interaction.followup.send(
                    f"❌ Không thể quay lúc này: `{str(error)[:300]}`", ephemeral=True
                )

    @discord.ui.button(label="Quay ×1", emoji="🎟️", style=discord.ButtonStyle.secondary)
    async def pull_one(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._reroll(interaction, 1)

    @discord.ui.button(label="Quay ×10", emoji="🎰", style=discord.ButtonStyle.primary)
    async def pull_ten(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._reroll(interaction, 10)

    @discord.ui.button(label="Tỷ lệ", emoji="📊", style=discord.ButtonStyle.secondary)
    async def show_rates(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        pool = await self.cog.get_pool()
        await interaction.response.send_message(
            embed=build_rates_embed(pool), ephemeral=True
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.HTTPException, discord.NotFound):
                pass


@dataclass(frozen=True, slots=True)
class PreparedBlueArchiveGacha:
    banner: BlueArchiveBanner
    target: BlueArchiveStudent
    pulls: tuple[BlueArchivePull, ...]
    recruitment_points: int = 0
    account_balance: int | None = None
    point_cost: int = 0


class BlueArchiveGachaView(discord.ui.View):
    def __init__(
        self,
        cog: "LimbusGacha",
        owner_id: int,
        *,
        region: str,
        target_id: int,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.cog = cog
        self.owner_id = int(owner_id)
        self.region = str(region)
        self.target_id = int(target_id)
        self.message: discord.Message | None = None
        self.roll_lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "🎓 Panel này thuộc lượt tuyển sinh của người khác. Dùng `/gacha` để mở lượt riêng nhé.",
            ephemeral=True,
        )
        return False

    async def _reroll(self, interaction: discord.Interaction, count: int) -> None:
        if self.roll_lock.locked():
            return await interaction.response.send_message(
                "⏳ Lượt tuyển sinh trước vẫn đang được dựng ảnh.", ephemeral=True
            )
        await interaction.response.defer()
        async with self.roll_lock:
            try:
                if interaction.guild_id is None:
                    raise EconomyDisabled("Gacha economy chỉ dùng trong server.")
                prepared = await self.cog.prepare_blue_archive_gacha(
                    interaction.guild_id,
                    interaction.user.id,
                    count,
                    region=self.region,
                    target_value=str(self.target_id),
                    source_id=f"interaction:{interaction.id}",
                )
                await self.cog.present_blue_archive_gacha(
                    interaction,
                    prepared,
                    view=self,
                    edit_original=True,
                )
            except (InsufficientPoints, EconomyDisabled) as error:
                await interaction.followup.send(f"❌ {error}", ephemeral=True)
            except Exception as error:
                logger.exception("Không thể quay lại Blue Archive gacha")
                await interaction.followup.send(
                    f"❌ Không thể tuyển sinh lúc này: `{str(error)[:300]}`",
                    ephemeral=True,
                )

    @discord.ui.button(label="Quay ×1", emoji="🎟️", style=discord.ButtonStyle.secondary)
    async def pull_one(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._reroll(interaction, 1)

    @discord.ui.button(label="Quay ×10", emoji="🎓", style=discord.ButtonStyle.primary)
    async def pull_ten(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._reroll(interaction, 10)

    @discord.ui.button(label="Tỷ lệ", emoji="📊", style=discord.ButtonStyle.secondary)
    async def show_rates(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        banner = await self.cog.blue_archive.get_banner(self.region)
        target = banner.target(str(self.target_id))
        await interaction.response.send_message(
            embed=blue_archive_rates_embed(banner, target), ephemeral=True
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.HTTPException, discord.NotFound):
                pass


@dataclass(frozen=True, slots=True)
class PreparedFGOGacha:
    pool: FGOPool
    pulls: tuple[FGOPull, ...]
    account_balance: int | None = None
    point_cost: int = 0


class FGOGachaView(discord.ui.View):
    def __init__(self, cog: "LimbusGacha", owner_id: int, *, region: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.cog = cog
        self.owner_id = int(owner_id)
        self.region = str(region)
        self.message: discord.Message | None = None
        self.roll_lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "🔷 Panel triệu hồi này thuộc người khác. Dùng `/gacha` để mở lượt riêng nhé.",
            ephemeral=True,
        )
        return False

    async def _reroll(self, interaction: discord.Interaction, count: int) -> None:
        if self.roll_lock.locked():
            return await interaction.response.send_message(
                "⏳ Lượt triệu hồi trước vẫn đang được dựng ảnh.", ephemeral=True
            )
        await interaction.response.defer()
        async with self.roll_lock:
            try:
                if interaction.guild_id is None:
                    raise EconomyDisabled("Gacha economy chỉ dùng trong server.")
                prepared = await self.cog.prepare_fgo_gacha(
                    interaction.guild_id,
                    interaction.user.id,
                    count,
                    region=self.region,
                    source_id=f"interaction:{interaction.id}",
                )
                await self.cog.present_fgo_gacha(
                    interaction,
                    prepared,
                    view=self,
                    edit_original=True,
                )
            except (InsufficientPoints, EconomyDisabled, ValueError) as error:
                await interaction.followup.send(f"❌ {error}", ephemeral=True)
            except Exception as error:
                logger.exception("Không thể quay lại FGO gacha")
                await interaction.followup.send(
                    f"❌ Không thể triệu hồi lúc này: `{str(error)[:300]}`",
                    ephemeral=True,
                )

    @discord.ui.button(label="Quay ×1", emoji="🎟️", style=discord.ButtonStyle.secondary)
    async def pull_one(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._reroll(interaction, 1)

    @discord.ui.button(label="Quay ×11", emoji="🔷", style=discord.ButtonStyle.primary)
    async def pull_eleven(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._reroll(interaction, 11)

    @discord.ui.button(label="Tỷ lệ", emoji="📊", style=discord.ButtonStyle.secondary)
    async def show_rates(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        pool = await self.cog.fgo.get_pool(self.region)
        await interaction.response.send_message(
            embed=fgo_rates_embed(pool), ephemeral=True
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.HTTPException, discord.NotFound):
                pass


@dataclass(frozen=True, slots=True)
class PreparedBrownDust2Gacha:
    pool: BrownDust2Pool
    pulls: tuple[BrownDust2Pull, ...]
    pity: BrownDust2Pity
    account_balance: int | None = None
    point_cost: int = 0


class BrownDust2GachaView(discord.ui.View):
    def __init__(
        self,
        cog: "LimbusGacha",
        owner_id: int,
        *,
        pity: BrownDust2Pity,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.cog = cog
        self.owner_id = int(owner_id)
        self.pity = pity
        self.message: discord.Message | None = None
        self.roll_lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "✨ Panel Costume Draw này thuộc người khác. Dùng `/gacha` để mở lượt riêng nhé.",
            ephemeral=True,
        )
        return False

    async def _reroll(self, interaction: discord.Interaction, count: int) -> None:
        if self.roll_lock.locked():
            return await interaction.response.send_message(
                "⏳ Lượt Draw trước vẫn đang được dựng ảnh.", ephemeral=True
            )
        await interaction.response.defer()
        async with self.roll_lock:
            try:
                if interaction.guild_id is None:
                    raise EconomyDisabled("Gacha economy chỉ dùng trong server.")
                prepared = await self.cog.prepare_brown_dust_2_gacha(
                    interaction.guild_id,
                    interaction.user.id,
                    count,
                    source_id=f"interaction:{interaction.id}",
                    free_pity=self.pity,
                )
                await self.cog.present_brown_dust_2_gacha(
                    interaction,
                    prepared,
                    view=self,
                    edit_original=True,
                )
            except (InsufficientPoints, EconomyDisabled, ValueError) as error:
                await interaction.followup.send(f"❌ {error}", ephemeral=True)
            except Exception as error:
                logger.exception("Không thể quay lại Brown Dust 2 gacha")
                await interaction.followup.send(
                    f"❌ Không thể Costume Draw lúc này: `{str(error)[:300]}`",
                    ephemeral=True,
                )

    @discord.ui.button(label="Quay ×1", emoji="🎟️", style=discord.ButtonStyle.secondary)
    async def pull_one(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._reroll(interaction, 1)

    @discord.ui.button(label="Quay ×10", emoji="✨", style=discord.ButtonStyle.primary)
    async def pull_ten(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._reroll(interaction, 10)

    @discord.ui.button(label="Tỷ lệ", emoji="📊", style=discord.ButtonStyle.secondary)
    async def show_rates(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        pool = await self.cog.brown_dust_2.get_pool()
        await interaction.response.send_message(
            embed=brown_dust_2_rates_embed(pool), ephemeral=True
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.HTTPException, discord.NotFound):
                pass


class LimbusGacha(commands.Cog):
    exchange_group = app_commands.Group(
        name="exchange",
        description="Đổi Extraction Points lấy vật phẩm Limbus",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.pool: GachaPool | None = None
        self.pool_loaded_at = 0.0
        self.pool_lock = asyncio.Lock()
        self.exchange_catalog: GachaPool | None = None
        self.exchange_catalog_loaded_at = 0.0
        self.exchange_catalog_lock = asyncio.Lock()
        self.account_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self.image_semaphore = asyncio.Semaphore(10)
        self.art_cache_dir: Path | None = None
        self.economy_store = get_economy_store(bot)
        self.blue_archive = BlueArchiveGachaService()
        self.fgo = FGOGachaService()
        self.brown_dust_2 = BrownDust2GachaService()

    async def cog_load(self) -> None:
        # Chuẩn bị filesystem trước khi mở HTTP session để không rò session nếu
        # đường dẫn cache bị một file khác chiếm chỗ hoặc thiếu quyền ghi.
        self.art_cache_dir = await asyncio.to_thread(
            _prepare_art_cache_dir, ART_CACHE_DIR
        )
        await self.economy_store.init()
        timeout = aiohttp.ClientTimeout(
            total=IMAGE_TIMEOUT_SECONDS,
            connect=min(15, IMAGE_TIMEOUT_SECONDS),
            sock_read=max(15, IMAGE_TIMEOUT_SECONDS - 10),
        )
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                # User-Agent không giả trình duyệt để Cloudflare giữ nguyên PNG
                # thay vì tự tối ưu sang WebP theo khả năng của Chrome.
                "User-Agent": "PetoDiscordBot/1.0 (Limbus gacha artwork)",
                # Không xin AVIF/WebP: một số bản Pillow tối giản trên Windows/VPS
                # không có decoder tương ứng dù wiki CDN vẫn trả file hợp lệ.
                "Accept": "image/png,image/jpeg,*/*;q=0.1",
                "Referer": "https://limbuscompany.wiki.gg/",
            },
        )
        await self.blue_archive.open()
        await self.fgo.open()
        await self.brown_dust_2.open()

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()
        self.session = None
        await self.blue_archive.close()
        await self.fgo.close()
        await self.brown_dust_2.close()

    async def get_pool(self) -> GachaPool:
        now = time.monotonic()
        if self.pool and now - self.pool_loaded_at < POOL_REFRESH_SECONDS:
            return self.pool
        async with self.pool_lock:
            now = time.monotonic()
            if self.pool and now - self.pool_loaded_at < POOL_REFRESH_SECONDS:
                return self.pool
            self.pool = await asyncio.to_thread(load_gacha_pool_sync, DB_PATH)
            self.pool_loaded_at = now
            logger.info(
                "Limbus Gacha pool: 3★=%s, 2★=%s, 1★=%s, E.G.O=%s",
                len(self.pool.entries(KIND_ID3)),
                len(self.pool.entries(KIND_ID2)),
                len(self.pool.entries(KIND_ID1)),
                len(self.pool.entries(KIND_EGO)),
            )
            return self.pool

    async def get_exchange_catalog(self) -> GachaPool:
        now = time.monotonic()
        if (
            self.exchange_catalog
            and now - self.exchange_catalog_loaded_at < POOL_REFRESH_SECONDS
        ):
            return self.exchange_catalog
        async with self.exchange_catalog_lock:
            now = time.monotonic()
            if (
                self.exchange_catalog
                and now - self.exchange_catalog_loaded_at < POOL_REFRESH_SECONDS
            ):
                return self.exchange_catalog
            self.exchange_catalog = await asyncio.to_thread(
                load_exchange_catalog_sync, DB_PATH
            )
            self.exchange_catalog_loaded_at = now
            logger.info(
                "Limbus exchange catalog (không Walpurgis): 3★=%s, E.G.O=%s",
                len(self.exchange_catalog.entries(KIND_ID3)),
                len(self.exchange_catalog.entries(KIND_EGO)),
            )
            return self.exchange_catalog

    async def _download_image(self, url: str) -> bytes:
        if not url or not self.session:
            return b""
        cache_path = None
        if self.art_cache_dir:
            cache_path = self.art_cache_dir / (
                f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.img"
            )
            try:
                cached = await asyncio.to_thread(cache_path.read_bytes)
                if await asyncio.to_thread(_image_is_decodable, cached):
                    return cached
                # Cache từ phiên bản cũ có thể là WebP/AVIF mà Pillow hiện tại
                # không đọc được. Xóa đúng file đó để lần này tải lại PNG/JPEG.
                try:
                    await asyncio.to_thread(cache_path.unlink, missing_ok=True)
                except OSError:
                    pass
            except (FileNotFoundError, OSError):
                pass
        async with self.image_semaphore:
            last_error = ""
            for attempt in range(2):
                try:
                    async with self.session.get(url, allow_redirects=True) as response:
                        if response.status != 200:
                            last_error = f"HTTP {response.status}"
                        else:
                            content_type = response.headers.get("Content-Type", "")
                            if not content_type.lower().startswith("image/"):
                                last_error = f"Content-Type {content_type or 'unknown'}"
                            else:
                                data = await _read_limited_image_response(
                                    response, IMAGE_MAX_BYTES
                                )
                                if data is None:
                                    last_error = "file vượt giới hạn"
                                elif not await asyncio.to_thread(
                                    _image_is_decodable, data
                                ):
                                    last_error = "định dạng ảnh Pillow không giải mã được"
                                elif data:
                                    if cache_path:
                                        try:
                                            await asyncio.to_thread(cache_path.write_bytes, data)
                                        except OSError:
                                            logger.debug("Không ghi được Limbus art cache: %s", cache_path)
                                    return data
                except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                    last_error = str(error) or error.__class__.__name__
                    if isinstance(error, asyncio.TimeoutError):
                        break
                if attempt == 0:
                    await asyncio.sleep(1.0)
            logger.warning("Không tải được Limbus Gacha artwork (%s): %s", last_error, url)
            return b""

    async def _collage_file(
        self, pulls: Sequence[GachaEntry]
    ) -> discord.File | None:
        urls = tuple(dict.fromkeys(item.image_url for item in pulls if item.image_url))
        downloaded = await asyncio.gather(*(self._download_image(url) for url in urls))
        image_data = dict(zip(urls, downloaded))
        try:
            png = await asyncio.to_thread(render_gacha_collage, pulls, image_data)
        except Exception:
            logger.exception("Không thể dựng collage Limbus Gacha")
            return None
        filename = f"limbus-gacha-{int(time.time() * 1000)}.png"
        return discord.File(io.BytesIO(png), filename=filename)

    async def make_payload(
        self,
        count: int,
        *,
        pulls: Sequence[GachaEntry] | None = None,
        account: EconomyAccount | None = None,
        point_cost: int = 0,
    ) -> GachaPayload:
        pool = await self.get_pool()
        pulls = tuple(pulls or pull_entries(pool, count))
        if count == 1:
            entry = pulls[0]
            embed = discord.Embed(
                title=entry.name,
                url=entry.url,
                description=(
                    f"{RARITY_EMOJI[entry.kind]} **{RARITY_TEXT[entry.kind]}**\n\n"
                    "Kết quả từ **Standard Extraction**."
                ),
                color=discord.Color.from_rgb(*RARITY_COLOR[entry.kind]),
            )
            if entry.image_url:
                embed.set_image(url=entry.image_url)
            embed.set_footer(
                text=(
                    f"Còn {account.balance:,} Peto Points • "
                    f"Extraction Points {account.extraction_points:,}/200"
                    if account
                    else "Peto Gacha • Mô phỏng • Nguồn: Limbus Company Wiki"
                )
            )
            return GachaPayload(embed, None, pulls)

        counts = {kind: 0 for kind in (KIND_EGO, KIND_ID3, KIND_ID2, KIND_ID1)}
        lines: list[str] = []
        for index, entry in enumerate(pulls, start=1):
            counts[entry.kind] += 1
            lines.append(
                f"`{index:02d}` {RARITY_EMOJI[entry.kind]} {_escaped_link(entry)}"
            )
        embed = discord.Embed(
            title="🎰 Standard Extraction — 10 lượt",
            description="\n".join(lines),
            color=0xA68B5B,
        )
        embed.add_field(
            name="Tổng kết",
            value=(
                f"✨ E.G.O `{counts[KIND_EGO]}` • "
                f"3★ `{counts[KIND_ID3]}` • "
                f"2★ `{counts[KIND_ID2]}` • "
                f"1★ `{counts[KIND_ID1]}`"
            ),
            inline=False,
        )
        if account:
            embed.add_field(
                name="Peto Economy",
                value=(
                    f"Đã dùng `{point_cost:,}` điểm • "
                    f"Còn `{account.balance:,}` • "
                    f"Extraction Points `{account.extraction_points:,}/200`"
                ),
                inline=False,
            )
        file = await self._collage_file(pulls)
        if file:
            embed.set_image(url=f"attachment://{file.filename}")
        embed.set_footer(
            text=(
                "Lượt 10 bảo đảm 2★+ • Kết quả đã lưu vào collection"
                if account
                else "Lượt 10 bảo đảm 2★+ • Mô phỏng • Nguồn: Limbus Company Wiki"
            )
        )
        return GachaPayload(embed, file, pulls)

    async def perform_gacha(
        self,
        guild_id: int,
        user_id: int,
        count: int,
        *,
        source_id: str,
    ) -> GachaPayload:
        pool = await self.get_pool()
        setting = await self.economy_store.get_settings(guild_id)
        if not setting.enabled:
            return await self.make_payload(count, pulls=pull_entries(pool, count))

        # Serialize paid rolls for one account so simultaneous slash commands
        # cannot race the same balance and their transactions stay deterministic.
        account_lock = self.account_locks.setdefault(
            (int(guild_id), int(user_id)), asyncio.Lock()
        )
        async with account_lock:
            pulls = pull_entries(pool, count)
            point_cost = GACHA_POINT_COST[count]
            account = await self.economy_store.record_gacha(
                guild_id,
                user_id,
                point_cost=point_cost,
                results=[(entry.kind, entry.name) for entry in pulls],
                source_id=source_id,
            )
        return await self.make_payload(
            count,
            pulls=pulls,
            account=account,
            point_cost=point_cost,
        )

    async def prepare_blue_archive_gacha(
        self,
        guild_id: int,
        user_id: int,
        count: int,
        *,
        region: str,
        target_value: str | None,
        source_id: str,
    ) -> PreparedBlueArchiveGacha:
        banner = await self.blue_archive.get_banner(region)
        target = banner.target(target_value)
        setting = await self.economy_store.get_settings(guild_id)
        if not setting.enabled:
            return PreparedBlueArchiveGacha(
                banner=banner,
                target=target,
                pulls=pull_blue_archive(banner, target, count),
            )

        account_lock = self.account_locks.setdefault(
            (int(guild_id), int(user_id)), asyncio.Lock()
        )
        async with account_lock:
            pulls = pull_blue_archive(banner, target, count)
            owned_by_kind = {
                kind: await self.economy_store.owned_names(
                    guild_id,
                    user_id,
                    kind,
                    game_id=BLUE_ARCHIVE_GAME_ID,
                )
                for kind in ("ba1", "ba2", "ba3")
            }
            pulls = mark_new_blue_archive_pulls(pulls, owned_by_kind)
            point_cost = GACHA_POINT_COST[count]
            account = await self.economy_store.record_gacha(
                guild_id,
                user_id,
                point_cost=point_cost,
                results=[(pull.student.kind, pull.student.name) for pull in pulls],
                source_id=source_id,
                game_id=BLUE_ARCHIVE_GAME_ID,
                banner_id=banner.banner_id,
                extraction_points_awarded=0,
                recruitment_points_awarded=count,
            )
            recruitment_points = await self.economy_store.gacha_pity_points(
                guild_id,
                user_id,
                game_id=BLUE_ARCHIVE_GAME_ID,
                banner_id=banner.banner_id,
            )
        return PreparedBlueArchiveGacha(
            banner=banner,
            target=target,
            pulls=pulls,
            recruitment_points=recruitment_points,
            account_balance=account.balance,
            point_cost=point_cost,
        )

    async def present_blue_archive_gacha(
        self,
        interaction: discord.Interaction,
        prepared: PreparedBlueArchiveGacha,
        *,
        view: BlueArchiveGachaView,
        edit_original: bool,
    ) -> discord.Message | None:
        payload = await self.blue_archive.make_payload(
            prepared.banner,
            prepared.target,
            prepared.pulls,
            recruitment_points=prepared.recruitment_points,
            account_balance=prepared.account_balance,
            point_cost=prepared.point_cost,
        )
        if edit_original:
            await interaction.edit_original_response(
                embed=payload.embed,
                attachments=[payload.file],
                view=view,
            )
            message = interaction.message
        else:
            message = await interaction.followup.send(
                embed=payload.embed,
                file=payload.file,
                view=view,
                wait=True,
            )
        view.message = message
        return message

    async def prepare_fgo_gacha(
        self,
        guild_id: int,
        user_id: int,
        count: int,
        *,
        region: str,
        source_id: str,
    ) -> PreparedFGOGacha:
        pool = await self.fgo.get_pool(region)
        setting = await self.economy_store.get_settings(guild_id)
        if not setting.enabled:
            return PreparedFGOGacha(pool=pool, pulls=pull_fgo(pool, count))

        account_lock = self.account_locks.setdefault(
            (int(guild_id), int(user_id)), asyncio.Lock()
        )
        async with account_lock:
            pulls = pull_fgo(pool, count)
            fgo_kinds = (
                KIND_SERVANT_3,
                KIND_SERVANT_4,
                KIND_SERVANT_5,
                KIND_CE_3,
                KIND_CE_4,
                KIND_CE_5,
            )
            owned_by_kind = {
                kind: await self.economy_store.owned_names(
                    guild_id,
                    user_id,
                    kind,
                    game_id=FGO_GAME_ID,
                )
                for kind in fgo_kinds
            }
            pulls = mark_new_fgo_pulls(pulls, owned_by_kind)
            point_cost = GACHA_POINT_COST[count]
            account = await self.economy_store.record_gacha(
                guild_id,
                user_id,
                point_cost=point_cost,
                results=[(pull.card.kind, pull.card.name) for pull in pulls],
                source_id=source_id,
                game_id=FGO_GAME_ID,
                banner_id=pool.banner_id,
                extraction_points_awarded=0,
                recruitment_points_awarded=0,
            )
        return PreparedFGOGacha(
            pool=pool,
            pulls=pulls,
            account_balance=account.balance,
            point_cost=point_cost,
        )

    async def present_fgo_gacha(
        self,
        interaction: discord.Interaction,
        prepared: PreparedFGOGacha,
        *,
        view: FGOGachaView,
        edit_original: bool,
    ) -> discord.Message | None:
        payload = await self.fgo.make_payload(
            prepared.pool,
            prepared.pulls,
            account_balance=prepared.account_balance,
            point_cost=prepared.point_cost,
        )
        if edit_original:
            await interaction.edit_original_response(
                embed=payload.embed,
                attachments=[payload.file],
                view=view,
            )
            message = interaction.message
        else:
            message = await interaction.followup.send(
                embed=payload.embed,
                file=payload.file,
                view=view,
                wait=True,
            )
        view.message = message
        return message

    async def prepare_brown_dust_2_gacha(
        self,
        guild_id: int,
        user_id: int,
        count: int,
        *,
        source_id: str,
        free_pity: BrownDust2Pity | None = None,
    ) -> PreparedBrownDust2Gacha:
        pool = await self.brown_dust_2.get_pool()
        setting = await self.economy_store.get_settings(guild_id)
        if not setting.enabled:
            pulls, pity = pull_brown_dust_2(pool, count, pity=free_pity)
            return PreparedBrownDust2Gacha(pool=pool, pulls=pulls, pity=pity)

        account_lock = self.account_locks.setdefault(
            (int(guild_id), int(user_id)), asyncio.Lock()
        )
        async with account_lock:
            counters = await self.economy_store.gacha_counter_values(
                guild_id,
                user_id,
                game_id=BROWN_DUST_2_GAME_ID,
                banner_id=pool.banner_id,
            )
            previous_pity = BrownDust2Pity(
                since_four_star=counters.get("since_four_star", 0),
                since_five_star=counters.get("since_five_star", 0),
            )
            pulls, pity = pull_brown_dust_2(pool, count, pity=previous_pity)
            owned_by_kind = {
                kind: await self.economy_store.owned_names(
                    guild_id,
                    user_id,
                    kind,
                    game_id=BROWN_DUST_2_GAME_ID,
                )
                for kind in (BD2_KIND_STAR3, BD2_KIND_STAR4, BD2_KIND_STAR5)
            }
            pulls = mark_new_brown_dust_2_pulls(pulls, owned_by_kind)
            point_cost = GACHA_POINT_COST[count]
            account = await self.economy_store.record_gacha(
                guild_id,
                user_id,
                point_cost=point_cost,
                results=[(pull.costume.kind, pull.costume.name) for pull in pulls],
                source_id=source_id,
                game_id=BROWN_DUST_2_GAME_ID,
                banner_id=pool.banner_id,
                extraction_points_awarded=0,
                recruitment_points_awarded=0,
                counter_updates={
                    "since_four_star": pity.since_four_star,
                    "since_five_star": pity.since_five_star,
                },
            )
        return PreparedBrownDust2Gacha(
            pool=pool,
            pulls=pulls,
            pity=pity,
            account_balance=account.balance,
            point_cost=point_cost,
        )

    async def present_brown_dust_2_gacha(
        self,
        interaction: discord.Interaction,
        prepared: PreparedBrownDust2Gacha,
        *,
        view: BrownDust2GachaView,
        edit_original: bool,
    ) -> discord.Message | None:
        payload = await self.brown_dust_2.make_payload(
            prepared.pool,
            prepared.pulls,
            pity=prepared.pity,
            account_balance=prepared.account_balance,
            point_cost=prepared.point_cost,
        )
        view.pity = prepared.pity
        if edit_original:
            await interaction.edit_original_response(
                embed=payload.embed,
                attachments=[payload.file],
                view=view,
            )
            message = interaction.message
        else:
            message = await interaction.followup.send(
                embed=payload.embed,
                file=payload.file,
                view=view,
                wait=True,
            )
        view.message = message
        return message

    async def blue_archive_target_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        namespace = interaction.namespace
        game = str(getattr(namespace, "game", "limbus") or "limbus")
        if game != BLUE_ARCHIVE_GAME_ID:
            return []
        region = str(getattr(namespace, "server", "global") or "global")
        try:
            banner = await self.blue_archive.get_banner(region)
        except Exception:
            return []
        needle = current.strip().casefold()
        return [
            app_commands.Choice(
                name=f"{student.name} — 3★ Pickup"[:100],
                value=str(student.student_id),
            )
            for student in banner.pickups
            if not needle or needle in student.name.casefold()
        ][:25]

    async def exchange_identity_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        try:
            pool = await self.get_exchange_catalog()
            owned = await self.economy_store.owned_names(
                interaction.guild_id, interaction.user.id, KIND_ID3
            )
        except Exception:
            return []
        needle = _roster_key(current)
        return [
            app_commands.Choice(name=entry.name[:100], value=entry.name[:100])
            for entry in pool.entries(KIND_ID3)
            if entry.name not in owned and (not needle or needle in _roster_key(entry.name))
        ][:25]

    @exchange_group.command(
        name="identity",
        description="Đổi 200 Extraction Points lấy một Identity 3★ chưa sở hữu",
    )
    @app_commands.guild_only()
    @app_commands.describe(identity="Identity 3★ cần đổi")
    @app_commands.autocomplete(identity=exchange_identity_autocomplete)
    async def exchange_identity(
        self, interaction: discord.Interaction, identity: str
    ) -> None:
        assert interaction.guild_id is not None
        await interaction.response.defer(thinking=True)
        try:
            pool = await self.get_exchange_catalog()
            entry = next(
                (
                    candidate
                    for candidate in pool.entries(KIND_ID3)
                    if _roster_key(candidate.name) == _roster_key(identity)
                ),
                None,
            )
            if entry is None:
                return await interaction.followup.send(
                    "❌ Identity 3★ này không có trong danh mục đổi. "
                    "Identity Walpurgis không thể đổi.",
                    ephemeral=True,
                )
            account = await self.economy_store.exchange_item(
                interaction.guild_id,
                interaction.user.id,
                item_kind=KIND_ID3,
                item_name=entry.name,
                extraction_cost=EXTRACTION_EXCHANGE_COST,
                source_id=f"interaction:{interaction.id}",
            )
        except (EconomyDisabled, InsufficientExtractionPoints, AlreadyOwned) as error:
            return await interaction.followup.send(f"❌ {error}", ephemeral=True)
        except Exception as error:
            logger.exception("Không đổi được Identity 3★")
            return await interaction.followup.send(
                f"❌ Không thể đổi Identity lúc này: `{str(error)[:250]}`",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="✅ Đổi Identity thành công",
            description=(
                f"{RARITY_EMOJI[KIND_ID3]} **[{discord.utils.escape_markdown(entry.name)}]"
                f"(<{entry.url}>)** đã được thêm vào collection.\n\n"
                f"Extraction Points còn lại: **{account.extraction_points:,}**"
            ),
            color=discord.Color.from_rgb(*RARITY_COLOR[KIND_ID3]),
        )
        if entry.image_url:
            embed.set_image(url=entry.image_url)
        await interaction.followup.send(embed=embed)

    async def exchange_ego_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        try:
            catalog = await self.get_exchange_catalog()
            owned = await self.economy_store.owned_names(
                interaction.guild_id, interaction.user.id, KIND_EGO
            )
        except Exception:
            return []
        needle = _roster_key(current)
        return [
            app_commands.Choice(name=entry.name[:100], value=entry.name[:100])
            for entry in catalog.entries(KIND_EGO)
            if entry.name not in owned and (not needle or needle in _roster_key(entry.name))
        ][:25]

    @exchange_group.command(
        name="ego",
        description="Đổi 200 Extraction Points lấy một E.G.O chưa sở hữu",
    )
    @app_commands.guild_only()
    @app_commands.describe(ego="E.G.O cần đổi; không hỗ trợ Walpurgis")
    @app_commands.autocomplete(ego=exchange_ego_autocomplete)
    async def exchange_ego(
        self, interaction: discord.Interaction, ego: str
    ) -> None:
        assert interaction.guild_id is not None
        await interaction.response.defer(thinking=True)
        try:
            catalog = await self.get_exchange_catalog()
            entry = next(
                (
                    candidate
                    for candidate in catalog.entries(KIND_EGO)
                    if _roster_key(candidate.name) == _roster_key(ego)
                ),
                None,
            )
            if entry is None:
                return await interaction.followup.send(
                    "❌ E.G.O này không có trong danh mục đổi. "
                    "E.G.O Walpurgis không thể đổi.",
                    ephemeral=True,
                )
            account = await self.economy_store.exchange_item(
                interaction.guild_id,
                interaction.user.id,
                item_kind=KIND_EGO,
                item_name=entry.name,
                extraction_cost=EXTRACTION_EXCHANGE_COST,
                source_id=f"interaction:{interaction.id}",
            )
        except (EconomyDisabled, InsufficientExtractionPoints, AlreadyOwned) as error:
            return await interaction.followup.send(f"❌ {error}", ephemeral=True)
        except Exception as error:
            logger.exception("Không đổi được E.G.O")
            return await interaction.followup.send(
                f"❌ Không thể đổi E.G.O lúc này: `{str(error)[:250]}`",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="✅ Đổi E.G.O thành công",
            description=(
                f"{RARITY_EMOJI[KIND_EGO]} **[{discord.utils.escape_markdown(entry.name)}]"
                f"(<{entry.url}>)** đã được thêm vào collection.\n\n"
                f"Extraction Points còn lại: **{account.extraction_points:,}**"
            ),
            color=discord.Color.from_rgb(*RARITY_COLOR[KIND_EGO]),
        )
        if entry.image_url:
            embed.set_image(url=entry.image_url)
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="gacha",
        description="Gacha Limbus, Blue Archive, FGO hoặc Brown Dust 2",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        game="Game cần quay; mặc định giữ nguyên Limbus Company",
        pulls="Số lượt quay; FGO dùng ×1/×11, game khác dùng ×1/×10",
        server="Server Blue Archive/FGO; game khác sẽ bỏ qua",
        target="Học sinh pickup mục tiêu của Blue Archive",
    )
    @app_commands.choices(
        game=[
            app_commands.Choice(name="Limbus Company", value="limbus"),
            app_commands.Choice(name="Blue Archive", value=BLUE_ARCHIVE_GAME_ID),
            app_commands.Choice(name="Fate/Grand Order", value=FGO_GAME_ID),
            app_commands.Choice(name="Brown Dust 2", value=BROWN_DUST_2_GAME_ID),
        ],
        pulls=[
            app_commands.Choice(name="Quay ×1", value=1),
            app_commands.Choice(name="Quay ×10", value=10),
            app_commands.Choice(name="Quay ×11 (FGO)", value=11),
        ],
        server=[
            app_commands.Choice(name="Global", value="global"),
            app_commands.Choice(name="JP", value="jp"),
            app_commands.Choice(name="CN", value="cn"),
        ],
    )
    @app_commands.autocomplete(target=blue_archive_target_autocomplete)
    async def gacha(
        self,
        interaction: discord.Interaction,
        game: app_commands.Choice[str] | None = None,
        pulls: app_commands.Choice[int] | None = None,
        server: app_commands.Choice[str] | None = None,
        target: str | None = None,
    ) -> None:
        game_id = game.value if game else "limbus"
        count = pulls.value if pulls else (11 if game_id == FGO_GAME_ID else 10)
        await interaction.response.defer(thinking=True)
        if game_id == BLUE_ARCHIVE_GAME_ID:
            try:
                if count not in {1, 10}:
                    raise ValueError("Blue Archive chỉ hỗ trợ quay ×1 hoặc ×10.")
                assert interaction.guild_id is not None
                region = server.value if server else "global"
                prepared = await self.prepare_blue_archive_gacha(
                    interaction.guild_id,
                    interaction.user.id,
                    count,
                    region=region,
                    target_value=target,
                    source_id=f"interaction:{interaction.id}",
                )
                view = BlueArchiveGachaView(
                    self,
                    interaction.user.id,
                    region=prepared.banner.region,
                    target_id=prepared.target.student_id,
                )
                view.message = await self.present_blue_archive_gacha(
                    interaction,
                    prepared,
                    view=view,
                    edit_original=False,
                )
                return
            except (InsufficientPoints, EconomyDisabled, ValueError) as error:
                return await interaction.followup.send(f"❌ {error}", ephemeral=True)
            except Exception as error:
                logger.exception("Không thể chạy /gacha Blue Archive")
                return await interaction.followup.send(
                    "❌ Blue Archive gacha chưa sẵn sàng. "
                    f"`{str(error)[:350]}`",
                    ephemeral=True,
                )
        if game_id == FGO_GAME_ID:
            try:
                if count not in {1, 11}:
                    raise ValueError("FGO chỉ hỗ trợ quay ×1 hoặc ×11.")
                assert interaction.guild_id is not None
                region = server.value if server else "global"
                if region == "cn":
                    raise ValueError("FGO hiện hỗ trợ server NA/Global hoặc JP.")
                prepared = await self.prepare_fgo_gacha(
                    interaction.guild_id,
                    interaction.user.id,
                    count,
                    region=region,
                    source_id=f"interaction:{interaction.id}",
                )
                view = FGOGachaView(
                    self,
                    interaction.user.id,
                    region=prepared.pool.region,
                )
                view.message = await self.present_fgo_gacha(
                    interaction,
                    prepared,
                    view=view,
                    edit_original=False,
                )
                return
            except (InsufficientPoints, EconomyDisabled, ValueError) as error:
                return await interaction.followup.send(f"❌ {error}", ephemeral=True)
            except Exception as error:
                logger.exception("Không thể chạy /gacha FGO")
                return await interaction.followup.send(
                    "❌ FGO gacha chưa sẵn sàng. "
                    f"`{str(error)[:350]}`",
                    ephemeral=True,
                )
        if game_id == BROWN_DUST_2_GAME_ID:
            try:
                if count not in {1, 10}:
                    raise ValueError("Brown Dust 2 chỉ hỗ trợ quay ×1 hoặc ×10.")
                assert interaction.guild_id is not None
                prepared = await self.prepare_brown_dust_2_gacha(
                    interaction.guild_id,
                    interaction.user.id,
                    count,
                    source_id=f"interaction:{interaction.id}",
                )
                view = BrownDust2GachaView(
                    self,
                    interaction.user.id,
                    pity=prepared.pity,
                )
                view.message = await self.present_brown_dust_2_gacha(
                    interaction,
                    prepared,
                    view=view,
                    edit_original=False,
                )
                return
            except (InsufficientPoints, EconomyDisabled, ValueError) as error:
                return await interaction.followup.send(f"❌ {error}", ephemeral=True)
            except Exception as error:
                logger.exception("Không thể chạy /gacha Brown Dust 2")
                return await interaction.followup.send(
                    "❌ Brown Dust 2 gacha chưa sẵn sàng. "
                    f"`{str(error)[:350]}`",
                    ephemeral=True,
                )
        try:
            if count not in {1, 10}:
                raise ValueError("Limbus Company chỉ hỗ trợ quay ×1 hoặc ×10.")
            assert interaction.guild_id is not None
            payload = await self.perform_gacha(
                interaction.guild_id,
                interaction.user.id,
                count,
                source_id=f"interaction:{interaction.id}",
            )
        except (InsufficientPoints, EconomyDisabled, ValueError) as error:
            return await interaction.followup.send(f"❌ {error}", ephemeral=True)
        except Exception as error:
            logger.exception("Không thể chạy /gacha")
            return await interaction.followup.send(
                "❌ Gacha chưa sẵn sàng. "
                f"`{str(error)[:350]}`\n"
                "Hãy chờ Limbus Wiki sync xong rồi thử lại.",
                ephemeral=True,
            )
        view = LimbusGachaView(self, interaction.user.id)
        kwargs: dict = {"embed": payload.embed, "view": view, "wait": True}
        if payload.file:
            kwargs["file"] = payload.file
        view.message = await interaction.followup.send(**kwargs)


async def setup(bot: commands.Bot) -> None:
    if not _env_bool("LIMBUS_GACHA_ENABLED", True):
        logger.info("Limbus Gacha đang tắt bằng LIMBUS_GACHA_ENABLED=false")
        return
    await bot.add_cog(LimbusGacha(bot))
