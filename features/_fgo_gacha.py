"""Fate/Grand Order archive summon data, rolling and result rendering.

This helper intentionally uses Atlas Academy's public static exports instead of
scraping a wiki.  ``features.limbus_gacha`` owns the public ``/gacha`` command;
the leading underscore prevents this module from being auto-loaded as a Discord
extension.

The initial banner is clearly labelled ``Chaldea Archive``.  It is a simulator
pool made from released NA/JP metadata, not a claim that it mirrors a currently
running in-game pickup banner.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


logger = logging.getLogger(__name__)

GAME_ID = "fgo"
REGION_LABELS = {"na": "NA/Global", "jp": "JP"}
ATLAS_EXPORT_ROOT = "https://api.atlasacademy.io/export"
DATA_CACHE_SECONDS = 6 * 60 * 60
IMAGE_MAX_BYTES = 8 * 1024 * 1024
CANVAS_SIZE = (1280, 720)
UI_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "fgo_gacha"

KIND_SERVANT_3 = "fgo_svt3"
KIND_SERVANT_4 = "fgo_svt4"
KIND_SERVANT_5 = "fgo_svt5"
KIND_CE_3 = "fgo_ce3"
KIND_CE_4 = "fgo_ce4"
KIND_CE_5 = "fgo_ce5"

ART_CACHE_DIR = Path(
    os.getenv("FGO_GACHA_ART_CACHE_DIR", "fgo_gacha_art_cache")
).resolve()
DATA_CACHE_DIR = Path(
    os.getenv("FGO_GACHA_DATA_CACHE_DIR", "fgo_gacha_data_cache")
).resolve()


@dataclass(frozen=True, slots=True)
class FGOCard:
    card_id: int
    collection_no: int
    name: str
    rarity: int
    category: str
    image_url: str
    class_name: str = ""

    @property
    def kind(self) -> str:
        prefix = "fgo_svt" if self.category == "servant" else "fgo_ce"
        return f"{prefix}{self.rarity}"


@dataclass(frozen=True, slots=True)
class FGOPool:
    region: str
    banner_id: str
    servants: Mapping[int, tuple[FGOCard, ...]]
    craft_essences: Mapping[int, tuple[FGOCard, ...]]

    def cards(self, category: str, rarity: int) -> tuple[FGOCard, ...]:
        source = self.servants if category == "servant" else self.craft_essences
        return source.get(int(rarity), ())


@dataclass(frozen=True, slots=True)
class FGOPull:
    card: FGOCard
    is_new: bool = False


@dataclass(frozen=True, slots=True)
class FGOPayload:
    embed: discord.Embed
    file: discord.File
    pulls: tuple[FGOPull, ...]
    pool: FGOPool


def _region_value(value: str | None) -> str:
    normalized = str(value or "na").strip().casefold()
    normalized = {"global": "na", "en": "na"}.get(normalized, normalized)
    if normalized not in REGION_LABELS:
        raise ValueError("FGO hiện hỗ trợ server NA/Global hoặc JP.")
    return normalized


def _card_from_json(raw: Mapping[str, object], category: str) -> FGOCard | None:
    try:
        card_id = int(raw.get("id", 0))
        collection_no = int(raw.get("collectionNo", 0))
        rarity = int(raw.get("rarity", 0))
        name = str(raw.get("name") or "").strip()
        image_url = str(raw.get("face") or "").strip()
    except (TypeError, ValueError):
        return None
    if card_id <= 0 or collection_no <= 0 or rarity not in {3, 4, 5}:
        return None
    if not name or not image_url:
        return None
    if category == "servant":
        if str(raw.get("type") or "") != "normal":
            return None
        # Atlas uses this flag for special duplicate/internal forms which are
        # not independent summon results.
        if str(raw.get("flag") or "normal") != "normal":
            return None
    elif str(raw.get("flag") or "") != "normal":
        # Excludes Bond/Event/EXP/Valentine and other non-standard CEs.
        return None
    class_name = str(raw.get("className") or "").strip() if category == "servant" else ""
    return FGOCard(
        card_id,
        collection_no,
        name,
        rarity,
        category,
        image_url,
        class_name,
    )


def parse_fgo_pool(
    servants_payload: Sequence[Mapping[str, object]],
    equips_payload: Sequence[Mapping[str, object]],
    region: str = "na",
) -> FGOPool:
    """Build a complete, deterministic archive pool from Atlas basic exports."""

    region = _region_value(region)
    servant_groups: dict[int, list[FGOCard]] = {3: [], 4: [], 5: []}
    equip_groups: dict[int, list[FGOCard]] = {3: [], 4: [], 5: []}
    for raw in servants_payload:
        card = _card_from_json(raw, "servant")
        if card:
            servant_groups[card.rarity].append(card)
    for raw in equips_payload:
        card = _card_from_json(raw, "ce")
        if card:
            equip_groups[card.rarity].append(card)

    if any(not servant_groups[rarity] for rarity in (3, 4, 5)):
        raise RuntimeError("Atlas Academy trả pool Servant FGO không đầy đủ.")
    if any(not equip_groups[rarity] for rarity in (3, 4, 5)):
        raise RuntimeError("Atlas Academy trả pool Craft Essence FGO không đầy đủ.")

    servants = {
        rarity: tuple(sorted(cards, key=lambda card: (card.collection_no, card.card_id)))
        for rarity, cards in servant_groups.items()
    }
    craft_essences = {
        rarity: tuple(sorted(cards, key=lambda card: (card.collection_no, card.card_id)))
        for rarity, cards in equip_groups.items()
    }
    return FGOPool(
        region=region,
        banner_id=f"chaldea-archive:{region}",
        servants=servants,
        craft_essences=craft_essences,
    )


def _standard_card(pool: FGOPool, rng: random.Random) -> FGOCard:
    # Reference simulator rates: SSR Servant 1%, SSR CE 4%, SR Servant 3%,
    # SR CE 12%, R Servant 40%, R CE 40%.
    value = rng.random()
    if value < 0.01:
        category, rarity = "servant", 5
    elif value < 0.05:
        category, rarity = "ce", 5
    elif value < 0.08:
        category, rarity = "servant", 4
    elif value < 0.20:
        category, rarity = "ce", 4
    elif value < 0.60:
        category, rarity = "servant", 3
    else:
        category, rarity = "ce", 3
    return rng.choice(pool.cards(category, rarity))


def _guaranteed_servant(pool: FGOPool, rng: random.Random) -> FGOCard:
    value = rng.random()
    rarity = 5 if value < 0.01 else 4 if value < 0.04 else 3
    return rng.choice(pool.cards("servant", rarity))


def _guaranteed_four_star(pool: FGOPool, rng: random.Random) -> FGOCard:
    value = rng.random()
    if value < 0.05:
        category, rarity = "servant", 5
    elif value < 0.25:
        category, rarity = "ce", 5
    elif value < 0.40:
        category, rarity = "servant", 4
    else:
        category, rarity = "ce", 4
    return rng.choice(pool.cards(category, rarity))


def pull_fgo(
    pool: FGOPool,
    count: int,
    rng: random.Random | random.SystemRandom | None = None,
) -> tuple[FGOPull, ...]:
    """Roll ×1 or FGO's ×11 with a Servant and a 4★+ guarantee."""

    if count not in {1, 11}:
        raise ValueError("FGO chỉ hỗ trợ quay ×1 hoặc ×11.")
    rng = rng or random.SystemRandom()
    cards = [_standard_card(pool, rng) for _ in range(1 if count == 1 else 9)]
    if count == 11:
        cards.extend((_guaranteed_servant(pool, rng), _guaranteed_four_star(pool, rng)))
        rng.shuffle(cards)
    return tuple(FGOPull(card) for card in cards)


def mark_new_fgo_pulls(
    pulls: Sequence[FGOPull],
    owned_by_kind: Mapping[str, set[str]],
) -> tuple[FGOPull, ...]:
    seen = {
        (str(kind), str(name))
        for kind, names in owned_by_kind.items()
        for name in names
    }
    marked: list[FGOPull] = []
    for pull in pulls:
        key = (pull.card.kind, pull.card.name)
        is_new = key not in seen
        seen.add(key)
        marked.append(FGOPull(pull.card, is_new=is_new))
    return tuple(marked)


def _prepare_cache_dir(path: Path, label: str) -> Path | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path if path.is_dir() else None
    except OSError as error:
        logger.warning("Không tạo được %s tại %s: %s", label, path, error)
        return None


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path(os.environ.get("WINDIR", "C:/Windows"))
        / "Fonts"
        / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _summon_background() -> Image.Image:
    """Load the packaged summon room, with the original drawn fallback."""

    try:
        with Image.open(UI_ASSET_DIR / "summon_background.jpg") as source:
            return ImageOps.fit(
                source.convert("RGBA"),
                CANVAS_SIZE,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except (OSError, ValueError):
        logger.warning("Thiếu FGO summon background; dùng nền fallback tự dựng")

    width, height = CANVAS_SIZE
    image = Image.new("RGBA", CANVAS_SIZE, (4, 11, 28, 255))
    glow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow, "RGBA")
    for radius in range(520, 20, -12):
        alpha = max(0, round(34 * (1 - radius / 540) ** 1.5))
        draw.ellipse(
            (width // 2 - radius, height // 2 - radius, width // 2 + radius, height // 2 + radius),
            fill=(31, 126, 204, alpha),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(35))
    image.alpha_composite(glow)
    draw = ImageDraw.Draw(image, "RGBA")
    for radius in (310, 250, 190):
        box = (width // 2 - radius, height // 2 - radius, width // 2 + radius, height // 2 + radius)
        draw.ellipse(box, outline=(102, 198, 239, 36), width=2)
    for angle in range(0, 360, 30):
        radians = math.radians(angle)
        x = width // 2 + math.cos(radians) * 310
        y = height // 2 + math.sin(radians) * 310
        draw.line((width // 2, height // 2, x, y), fill=(92, 178, 222, 20), width=1)
    stars = random.Random(0xF60)
    for _ in range(170):
        x, y = stars.randrange(width), stars.randrange(height)
        size = stars.choice((1, 1, 1, 2, 2, 3))
        alpha = stars.randrange(55, 170)
        draw.ellipse((x - size, y - size, x + size, y + size), fill=(168, 227, 255, alpha))
    return image


def _portrait(raw: bytes, size: tuple[int, int]) -> Image.Image:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            return ImageOps.fit(
                source.convert("RGBA"), size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.42)
            )
    except Exception:
        fallback = Image.new("RGBA", size, (16, 29, 53, 255))
        draw = ImageDraw.Draw(fallback, "RGBA")
        draw.ellipse((size[0] // 3, 30, size[0] * 2 // 3, 95), fill=(84, 126, 166, 255))
        draw.rectangle((size[0] // 4, 90, size[0] * 3 // 4, size[1]), fill=(57, 91, 128, 255))
        return fallback


def _star_points(
    center_x: float,
    center_y: float,
    outer_radius: float,
    inner_radius: float,
) -> list[tuple[float, float]]:
    points = []
    for index in range(10):
        angle = math.radians(-90 + index * 36)
        radius = outer_radius if index % 2 == 0 else inner_radius
        points.append(
            (
                center_x + math.cos(angle) * radius,
                center_y + math.sin(angle) * radius,
            )
        )
    return points


def _draw_gold_stars(
    draw: ImageDraw.ImageDraw,
    *,
    center_x: int,
    center_y: int,
    rarity: int,
    radius: int,
) -> None:
    gap = max(1, radius // 3)
    total_width = rarity * radius * 2 + (rarity - 1) * gap
    start_x = center_x - total_width / 2 + radius
    for index in range(rarity):
        x = start_x + index * (radius * 2 + gap)
        points = _star_points(x, center_y, radius, radius * 0.46)
        shadow = [(px + 1, py + 1) for px, py in points]
        draw.polygon(shadow, fill=(39, 26, 4, 230))
        draw.polygon(points, fill=(255, 222, 39, 255), outline=(255, 250, 177, 255))


def _class_badge(card: FGOCard) -> str:
    if card.category != "servant":
        return ""
    compact = "".join(part[:1] for part in card.class_name.replace("-", " ").split())
    return (compact or "S")[:2].upper()


def _draw_card(
    canvas: Image.Image,
    pull: FGOPull,
    raw: bytes,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
) -> None:
    colors = {
        3: ((105, 118, 130), (226, 237, 242), (210, 218, 221)),
        4: ((153, 112, 22), (255, 225, 82), (232, 190, 46)),
        5: ((176, 126, 13), (255, 246, 137), (250, 205, 40)),
    }
    dark, bright, footer = colors[pull.card.rarity]
    glow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    glow_alpha = 45 if pull.card.rarity == 3 else 78 if pull.card.rarity == 4 else 118
    gd.rectangle((x - 5, y - 5, x + width + 5, y + height + 5), fill=(*bright, glow_alpha))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(9)))

    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rectangle(
        (x, y, x + width, y + height),
        fill=(*dark, 255),
        outline=(25, 27, 33, 255),
        width=max(2, width // 55),
    )
    inset = max(3, width // 40)
    draw.rectangle(
        (x + inset, y + inset, x + width - inset, y + height - inset),
        outline=(*bright, 255),
        width=max(2, width // 65),
    )
    art_margin = max(5, width // 25)
    footer_height = max(20, round(height * 0.14))
    art = _portrait(raw, (width - art_margin * 2, height - footer_height - art_margin * 2))
    canvas.alpha_composite(art, (x + art_margin, y + art_margin))
    footer_top = y + height - footer_height
    draw.rectangle(
        (x + art_margin, footer_top, x + width - art_margin, y + height - art_margin),
        fill=(*footer, 255),
    )
    category = "Servant" if pull.card.category == "servant" else "Craft Essence"
    font_size = max(9, round(width * 0.077))
    font = _font(font_size, bold=True)
    bbox = draw.textbbox((0, 0), category, font=font)
    draw.text(
        (x + (width - (bbox[2] - bbox[0])) // 2, footer_top + max(2, (footer_height - (bbox[3] - bbox[1])) // 2 - 1)),
        category,
        font=font,
        fill=(28, 25, 18, 255),
    )

    star_radius = max(6, round(width * 0.052))
    _draw_gold_stars(
        draw,
        center_x=x + width // 2,
        center_y=footer_top - star_radius - 1,
        rarity=pull.card.rarity,
        radius=star_radius,
    )

    badge = _class_badge(pull.card)
    if badge:
        badge_size = max(22, round(width * 0.19))
        cx = x + art_margin + badge_size // 2
        cy = y + art_margin + badge_size // 2
        diamond = ((cx, cy - badge_size // 2), (cx + badge_size // 2, cy), (cx, cy + badge_size // 2), (cx - badge_size // 2, cy))
        draw.polygon(diamond, fill=(43, 49, 57, 238), outline=(*bright, 255))
        badge_font = _font(max(8, badge_size // 3), bold=True)
        badge_box = draw.textbbox((0, 0), badge, font=badge_font)
        draw.text(
            (cx - (badge_box[2] - badge_box[0]) // 2, cy - (badge_box[3] - badge_box[1]) // 2 - 1),
            badge,
            font=badge_font,
            fill=(248, 248, 244, 255),
        )
    if pull.is_new:
        label = "NEW"
        new_font = _font(max(9, round(width * 0.075)), bold=True)
        bbox = draw.textbbox((0, 0), label, font=new_font)
        label_width = bbox[2] - bbox[0] + 12
        label_height = bbox[3] - bbox[1] + 7
        draw.rectangle(
            (x + width - label_width - 4, y + 4, x + width - 4, y + 4 + label_height),
            fill=(177, 31, 42, 244),
            outline=(255, 214, 91, 255),
        )
        draw.text(
            (x + width - label_width + 2, y + 6),
            label,
            font=new_font,
            fill=(255, 246, 208, 255),
        )


def render_fgo_result(
    pulls: Sequence[FGOPull],
    image_data: Mapping[str, bytes],
    *,
    region_label: str,
) -> bytes:
    canvas = _summon_background()
    pulls = tuple(pulls)
    if len(pulls) == 1:
        width, height = 264, 300
        positions = [((CANVAS_SIZE[0] - width) // 2, 210)]
    else:
        width, height = 132, 150
        gap = 14
        row_gap = 24
        positions: list[tuple[int, int]] = []
        for row, count in enumerate((6, 5)):
            total_width = count * width + (count - 1) * gap
            start_x = (CANVAS_SIZE[0] - total_width) // 2
            y = 220 + row * (height + row_gap)
            positions.extend((start_x + index * (width + gap), y) for index in range(count))
    for pull, (x, y) in zip(pulls, positions):
        _draw_card(canvas, pull, image_data.get(pull.card.image_url, b""), x=x, y=y, width=width, height=height)
    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


class FGOGachaService:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self._pool_cache: dict[str, tuple[float, FGOPool]] = {}
        self._pool_lock = asyncio.Lock()
        self._download_semaphore = asyncio.Semaphore(10)
        self.art_cache_dir: Path | None = None
        self.data_cache_dir: Path | None = None

    async def open(self) -> None:
        self.art_cache_dir = await asyncio.to_thread(_prepare_cache_dir, ART_CACHE_DIR, "FGO art cache")
        self.data_cache_dir = await asyncio.to_thread(_prepare_cache_dir, DATA_CACHE_DIR, "FGO data cache")
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45, connect=12, sock_read=35),
            headers={"User-Agent": "PetoDiscordBot/1.0 (FGO gacha; Atlas Academy)"},
        )

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        self.session = None

    def _export_url(self, region: str, kind: str) -> str:
        suffix = "_lang_en" if region == "jp" else ""
        return f"{ATLAS_EXPORT_ROOT}/{region.upper()}/basic_{kind}{suffix}.json"

    async def _fetch_list(self, url: str) -> list[Mapping[str, object]]:
        if not self.session:
            raise RuntimeError("FGO gacha chưa khởi tạo HTTP session.")
        async with self.session.get(url) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
        if not isinstance(payload, list):
            raise RuntimeError("Atlas Academy trả dữ liệu không hợp lệ.")
        return [item for item in payload if isinstance(item, Mapping)]

    async def _load_exports(self, region: str) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
        cache_path = self.data_cache_dir / f"{region}-basic.json" if self.data_cache_dir else None
        try:
            servants, equips = await asyncio.gather(
                self._fetch_list(self._export_url(region, "servant")),
                self._fetch_list(self._export_url(region, "equip")),
            )
            if cache_path:
                payload = json.dumps({"servants": servants, "equips": equips}, ensure_ascii=False)
                try:
                    await asyncio.to_thread(cache_path.write_text, payload, encoding="utf-8")
                except OSError as error:
                    logger.warning("Không ghi được FGO data cache %s: %s", cache_path, error)
            return servants, equips
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as error:
            if not cache_path:
                raise
            try:
                cached = json.loads(await asyncio.to_thread(cache_path.read_text, encoding="utf-8"))
                servants = cached.get("servants", [])
                equips = cached.get("equips", [])
                if isinstance(servants, list) and isinstance(equips, list):
                    logger.warning("Atlas Academy tạm lỗi; dùng FGO cache: %s", error)
                    return servants, equips
            except (OSError, ValueError, AttributeError):
                pass
            raise

    async def get_pool(self, region: str = "na") -> FGOPool:
        region = _region_value(region)
        cached = self._pool_cache.get(region)
        now = time.monotonic()
        if cached and now - cached[0] < DATA_CACHE_SECONDS:
            return cached[1]
        async with self._pool_lock:
            cached = self._pool_cache.get(region)
            now = time.monotonic()
            if cached and now - cached[0] < DATA_CACHE_SECONDS:
                return cached[1]
            servants, equips = await self._load_exports(region)
            pool = parse_fgo_pool(servants, equips, region)
            self._pool_cache[region] = (now, pool)
            return pool

    async def _download_image(self, url: str) -> bytes:
        if not self.session:
            return b""
        cache_path = None
        if self.art_cache_dir:
            cache_path = self.art_cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.img"
            try:
                return await asyncio.to_thread(cache_path.read_bytes)
            except (OSError, FileNotFoundError):
                pass
        async with self._download_semaphore:
            try:
                async with self.session.get(url) as response:
                    if response.status != 200:
                        return b""
                    if int(response.headers.get("Content-Length", 0) or 0) > IMAGE_MAX_BYTES:
                        return b""
                    data = await response.read()
                    if len(data) > IMAGE_MAX_BYTES:
                        return b""
                    if cache_path and data:
                        try:
                            await asyncio.to_thread(cache_path.write_bytes, data)
                        except OSError:
                            pass
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return b""

    async def make_payload(
        self,
        pool: FGOPool,
        pulls: Sequence[FGOPull],
        *,
        account_balance: int | None = None,
        point_cost: int = 0,
    ) -> FGOPayload:
        urls = tuple(dict.fromkeys(pull.card.image_url for pull in pulls))
        downloaded = await asyncio.gather(*(self._download_image(url) for url in urls))
        image_data = dict(zip(urls, downloaded))
        png = await asyncio.to_thread(
            render_fgo_result,
            pulls,
            image_data,
            region_label=REGION_LABELS[pool.region],
        )
        filename = f"fgo-gacha-{int(time.time() * 1000)}.png"
        file = discord.File(io.BytesIO(png), filename=filename)
        counts: dict[tuple[str, int], int] = {}
        lines = []
        for index, pull in enumerate(pulls, start=1):
            key = (pull.card.category, pull.card.rarity)
            counts[key] = counts.get(key, 0) + 1
            label = "Servant" if pull.card.category == "servant" else "CE"
            new = " 🆕" if pull.is_new else ""
            lines.append(
                f"`{index:02d}` {'★' * pull.card.rarity} **{discord.utils.escape_markdown(pull.card.name)}** ({label}){new}"
            )
        embed = discord.Embed(
            title=f"🔷 FGO Chaldea Archive — {len(pulls)} lượt",
            description="\n".join(lines),
            color=0x4FA6D8,
        )
        embed.add_field(
            name="Tổng kết",
            value=(
                f"Servant: 5★ `{counts.get(('servant', 5), 0)}` • 4★ `{counts.get(('servant', 4), 0)}` • 3★ `{counts.get(('servant', 3), 0)}`\n"
                f"CE: 5★ `{counts.get(('ce', 5), 0)}` • 4★ `{counts.get(('ce', 4), 0)}` • 3★ `{counts.get(('ce', 3), 0)}`"
            ),
            inline=False,
        )
        if account_balance is not None:
            embed.add_field(
                name="Peto Economy",
                value=f"Đã dùng `{point_cost:,}` điểm • Còn `{account_balance:,}` • Kết quả đã lưu vào collection",
                inline=False,
            )
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(
            text="Pool simulator tổng hợp • Dữ liệu/ảnh: Atlas Academy • ×11 bảo đảm 1 Servant và 1 thẻ 4★+"
        )
        return FGOPayload(embed, file, tuple(pulls), pool)


def fgo_rates_embed(pool: FGOPool) -> discord.Embed:
    embed = discord.Embed(
        title="📊 Tỷ lệ FGO Chaldea Archive",
        description=(
            "• 5★ Servant: `1%`\n"
            "• 5★ Craft Essence: `4%`\n"
            "• 4★ Servant: `3%`\n"
            "• 4★ Craft Essence: `12%`\n"
            "• 3★ Servant: `40%`\n"
            "• 3★ Craft Essence: `40%`\n\n"
            "Quay ×11 bảo đảm ít nhất **1 Servant 3★+** và **1 thẻ 4★+**."
        ),
        color=0x4FA6D8,
    )
    embed.add_field(
        name="Pool tổng hợp",
        value=(
            f"Servant `{sum(len(items) for items in pool.servants.values())}` • "
            f"Craft Essence `{sum(len(items) for items in pool.craft_essences.values())}` • "
            f"{REGION_LABELS[pool.region]}"
        ),
        inline=False,
    )
    embed.set_footer(text="Không phải banner pickup chính thức • Pool làm mới mỗi 6 giờ")
    return embed
