"""Brown Dust 2 Costume Draw data, rolling, and result rendering.

The public ``/gacha`` command is owned by :mod:`features.limbus_gacha`.  This
leading-underscore helper synchronizes the community wiki through its Cargo
API, keeps an offline data/art cache, and renders a Brown Dust 2-inspired
result screen without requiring ripped game UI assets.
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
from urllib.parse import quote

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


logger = logging.getLogger(__name__)

GAME_ID = "brown_dust_2"
WIKI_ROOT = "https://browndust2.miraheze.org"
API_URL = f"{WIKI_ROOT}/w/api.php"
DATA_CACHE_SECONDS = 6 * 60 * 60
IMAGE_MAX_BYTES = 8 * 1024 * 1024
CANVAS_SIZE = (1280, 720)

KIND_STAR3 = "bd2_3"
KIND_STAR4 = "bd2_4"
KIND_STAR5 = "bd2_5"
KIND_BY_STAR = {3: KIND_STAR3, 4: KIND_STAR4, 5: KIND_STAR5}

ART_CACHE_DIR = Path(
    os.getenv("BD2_GACHA_ART_CACHE_DIR", "bd2_gacha_art_cache")
).resolve()
DATA_CACHE_DIR = Path(
    os.getenv("BD2_GACHA_DATA_CACHE_DIR", "bd2_gacha_data_cache")
).resolve()


@dataclass(frozen=True, slots=True)
class BrownDust2Costume:
    costume_id: str
    character_name: str
    costume_name: str
    rarity: int
    page_url: str
    image_url: str
    fallback_image_url: str

    @property
    def name(self) -> str:
        return f"{self.character_name} — {self.costume_name}"

    @property
    def kind(self) -> str:
        return KIND_BY_STAR[self.rarity]


@dataclass(frozen=True, slots=True)
class BrownDust2Pool:
    banner_id: str
    by_rarity: Mapping[int, tuple[BrownDust2Costume, ...]]

    def costumes(self, rarity: int) -> tuple[BrownDust2Costume, ...]:
        return self.by_rarity.get(int(rarity), ())


@dataclass(frozen=True, slots=True)
class BrownDust2Pity:
    since_four_star: int = 0
    since_five_star: int = 0


@dataclass(frozen=True, slots=True)
class BrownDust2Pull:
    costume: BrownDust2Costume
    is_new: bool = False
    guaranteed_four_star: bool = False
    guaranteed_five_star: bool = False


@dataclass(frozen=True, slots=True)
class BrownDust2Payload:
    embed: discord.Embed
    file: discord.File
    pulls: tuple[BrownDust2Pull, ...]
    pool: BrownDust2Pool


def _truthy(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def _cargo_title(row: Mapping[str, object]) -> Mapping[str, object]:
    nested = row.get("title")
    return nested if isinstance(nested, Mapping) else row


def parse_brown_dust_2_pool(
    costume_rows: Sequence[Mapping[str, object]],
    companion_rows: Sequence[Mapping[str, object]],
) -> BrownDust2Pool:
    """Build the permanent, drawable, non-limited Costume pool."""

    stars: dict[str, int] = {}
    for raw in companion_rows:
        row = _cargo_title(raw)
        name = str(row.get("name") or row.get("Page") or "").strip()
        try:
            star = int(row.get("star", 0))
        except (TypeError, ValueError):
            continue
        if name and star in {3, 4, 5}:
            stars[name.casefold()] = star

    groups: dict[int, list[BrownDust2Costume]] = {3: [], 4: [], 5: []}
    seen_ids: set[str] = set()
    for raw in costume_rows:
        row = _cargo_title(raw)
        costume_id = str(row.get("id") or "").strip()
        character = str(row.get("charName") or "").strip()
        costume = str(row.get("name") or "").strip()
        page = str(row.get("Page") or "").strip()
        rarity = stars.get(character.casefold(), 0)
        if (
            not costume_id
            or costume_id in seen_ids
            or not character
            or not costume
            or rarity not in groups
            or not _truthy(row.get("isDrawable"))
            or _truthy(row.get("isLimited"))
        ):
            continue
        seen_ids.add(costume_id)
        encoded_page = quote((page or f"{character}/{costume}").replace(" ", "_"), safe="/'()-,.")
        # The inventory illustration is the square, upper-body portrait used by
        # Brown Dust 2's collection/result UI.  ``Costume_*.png`` is a tall
        # full-body splash and only makes a useful fallback when the wiki has
        # not uploaded the inventory crop yet.
        image_name = quote(f"Illust_inven_char{costume_id}.png", safe="._-")
        fallback_name = quote(f"Costume_{costume_id}.png", safe="._-")
        groups[rarity].append(
            BrownDust2Costume(
                costume_id=costume_id,
                character_name=character,
                costume_name=costume,
                rarity=rarity,
                page_url=f"{WIKI_ROOT}/wiki/{encoded_page}",
                image_url=f"{WIKI_ROOT}/wiki/Special:Redirect/file/{image_name}",
                fallback_image_url=(
                    f"{WIKI_ROOT}/wiki/Special:Redirect/file/{fallback_name}"
                ),
            )
        )

    minimums = {3: 5, 4: 5, 5: 5}
    missing = [
        f"{rarity}★={len(groups[rarity])}/{minimum}"
        for rarity, minimum in minimums.items()
        if len(groups[rarity]) < minimum
    ]
    if missing:
        raise RuntimeError("Brown Dust 2 Wiki trả pool chưa đầy đủ: " + ", ".join(missing))

    return BrownDust2Pool(
        banner_id="bd2-costume-draw-simulator",
        by_rarity={
            rarity: tuple(
                sorted(
                    items,
                    key=lambda item: (
                        item.character_name.casefold(),
                        item.costume_name.casefold(),
                        item.costume_id,
                    ),
                )
            )
            for rarity, items in groups.items()
        },
    )


def _roll_rarity(rng: random.Random, *, guaranteed_four: bool, guaranteed_five: bool) -> int:
    if guaranteed_five:
        return 5
    value = rng.random()
    if guaranteed_four:
        return 5 if value < 0.03 else 4
    if value < 0.03:
        return 5
    if value < 0.17:
        return 4
    return 3


def pull_brown_dust_2(
    pool: BrownDust2Pool,
    count: int,
    *,
    pity: BrownDust2Pity | None = None,
    rng: random.Random | random.SystemRandom | None = None,
) -> tuple[tuple[BrownDust2Pull, ...], BrownDust2Pity]:
    """Roll ×1/×10 and return the updated 4★/5★ pity counters."""

    if count not in {1, 10}:
        raise ValueError("Brown Dust 2 chỉ hỗ trợ quay ×1 hoặc ×10.")
    rng = rng or random.SystemRandom()
    state = pity or BrownDust2Pity()
    since_four = max(0, int(state.since_four_star))
    since_five = max(0, int(state.since_five_star))
    results: list[BrownDust2Pull] = []
    for _ in range(count):
        guaranteed_five = since_five >= 99
        guaranteed_four = not guaranteed_five and since_four >= 9
        rarity = _roll_rarity(
            rng,
            guaranteed_four=guaranteed_four,
            guaranteed_five=guaranteed_five,
        )
        candidates = pool.costumes(rarity)
        if not candidates:
            raise RuntimeError(f"Pool Brown Dust 2 {rarity}★ đang trống.")
        costume = rng.choice(candidates)
        results.append(
            BrownDust2Pull(
                costume,
                guaranteed_four_star=guaranteed_four,
                guaranteed_five_star=guaranteed_five,
            )
        )
        if rarity == 5:
            since_five = 0
            since_four = 0
        elif rarity == 4:
            since_five += 1
            since_four = 0
        else:
            since_five += 1
            since_four += 1
    return tuple(results), BrownDust2Pity(since_four, since_five)


def mark_new_brown_dust_2_pulls(
    pulls: Sequence[BrownDust2Pull],
    owned_by_kind: Mapping[str, set[str]],
) -> tuple[BrownDust2Pull, ...]:
    seen = {
        (str(kind), str(name))
        for kind, names in owned_by_kind.items()
        for name in names
    }
    marked: list[BrownDust2Pull] = []
    for pull in pulls:
        key = (pull.costume.kind, pull.costume.name)
        is_new = key not in seen
        seen.add(key)
        marked.append(
            BrownDust2Pull(
                pull.costume,
                is_new=is_new,
                guaranteed_four_star=pull.guaranteed_four_star,
                guaranteed_five_star=pull.guaranteed_five_star,
            )
        )
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


def _pearl_background(featured_raw: bytes = b"") -> Image.Image:
    width, height = CANVAS_SIZE
    image = Image.new("RGBA", CANVAS_SIZE, (247, 246, 249, 255))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            wave = math.sin(x / 115 + y / 88) * 3 + math.sin(x / 43 - y / 67) * 2
            distance = math.hypot(x - width * 0.52, y - height * 0.45)
            shade = int(max(-8, min(8, wave - distance / 260)))
            pixels[x, y] = (247 + shade, 246 + shade, min(255, 250 + shade), 255)
    veil = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(veil, "RGBA")
    draw.polygon(((0, 80), (420, 0), (690, 720), (220, 720)), fill=(228, 223, 238, 35))
    draw.polygon(((780, 0), (1280, 85), (1280, 620), (1000, 720)), fill=(255, 235, 246, 32))
    draw.ellipse((360, 80, 930, 650), fill=(255, 255, 255, 70))
    image.alpha_composite(veil.filter(ImageFilter.GaussianBlur(42)))

    # The real result screen leaves a very faint enlarged costume illustration
    # behind the cards.  Reusing the selected collection art keeps this dynamic
    # without requiring a bundled game background.
    if featured_raw:
        try:
            with Image.open(io.BytesIO(featured_raw)) as source:
                source.load()
                featured = ImageOps.fit(
                    source.convert("RGBA"),
                    CANVAS_SIZE,
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.42),
                )
            featured.putalpha(featured.getchannel("A").point(lambda alpha: alpha * 52 // 255))
            image.alpha_composite(featured.filter(ImageFilter.GaussianBlur(12)))
            image.alpha_composite(Image.new("RGBA", CANVAS_SIZE, (255, 255, 255, 72)))
        except (OSError, ValueError):
            pass
    return image


def _portrait(raw: bytes, size: tuple[int, int]) -> Image.Image:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            rgba = source.convert("RGBA")
            return ImageOps.fit(
                rgba,
                size,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except (OSError, ValueError):
        fallback = Image.new("RGBA", size, (224, 224, 230, 255))
        draw = ImageDraw.Draw(fallback, "RGBA")
        draw.ellipse((size[0] * 0.25, 25, size[0] * 0.75, size[0] * 0.75), fill=(170, 171, 182, 255))
        draw.polygon(
            ((size[0] // 2, size[1] // 3), (8, size[1]), (size[0] - 8, size[1])),
            fill=(154, 156, 168, 255),
        )
        return fallback


def _card_polygon(x: int, y: int, width: int, height: int, inset: int = 0) -> list[tuple[int, int]]:
    half = width // 2
    side = 7 + inset
    cap = 22 + inset
    tail = 36 + inset
    return [
        (x + half, y + inset),
        (x + width - side, y + cap),
        (x + width - side, y + height - tail),
        (x + half, y + height - inset),
        (x + side, y + height - tail),
        (x + side, y + cap),
    ]


def _draw_stars(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, rarity: int) -> None:
    radius = 7
    gap = 2
    total_width = rarity * radius * 2 + (rarity - 1) * gap
    first_x = x + (width - total_width) / 2 + radius
    center_y = y + radius + 2
    for star_index in range(rarity):
        center_x = first_x + star_index * (radius * 2 + gap)
        points: list[tuple[float, float]] = []
        for point_index in range(10):
            angle = math.radians(-90 + point_index * 36)
            point_radius = radius if point_index % 2 == 0 else radius * 0.45
            points.append(
                (
                    center_x + math.cos(angle) * point_radius,
                    center_y + math.sin(angle) * point_radius,
                )
            )
        draw.polygon(points, fill=(250, 206, 46, 255), outline=(133, 104, 24, 240))


def _draw_card(
    canvas: Image.Image,
    pull: BrownDust2Pull,
    raw: bytes,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
) -> None:
    rarity = pull.costume.rarity
    glow_colors = {3: (178, 181, 188), 4: (190, 87, 225), 5: (124, 226, 255)}
    glow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    alpha = 40 if rarity == 3 else 105 if rarity == 4 else 145
    if rarity >= 4:
        beam_color = (203, 97, 235) if rarity == 4 else (133, 234, 255)
        glow_draw.rounded_rectangle(
            (x - 9, y - 42, x + width + 9, y + height + 42),
            radius=width // 2,
            fill=(*beam_color, 28 if rarity == 4 else 36),
        )
    glow_draw.polygon(_card_polygon(x - 4, y - 4, width + 8, height + 8), fill=(*glow_colors[rarity], alpha))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(13)))

    draw = ImageDraw.Draw(canvas, "RGBA")
    outer = _card_polygon(x, y, width, height)
    draw.polygon(outer, fill=(241, 242, 246, 218))

    # Only the central portrait window is rectangular.  The pointed header and
    # footer are separate decorations, so every card remains vertically aligned
    # like the in-game Costume Draw result instead of becoming a tall hexagon.
    art_left = 9
    art_top = 39
    art_width = width - art_left * 2
    art_height = height - art_top - 52
    art = _portrait(raw, (art_width, art_height))
    card_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    card_layer.paste(art, (art_left, art_top), art)
    canvas.alpha_composite(card_layer, (x, y))

    # Keep the frame itself clean and white.  Rarity is conveyed by the soft
    # glow and star count; extra inner, purple, or rainbow strokes made the
    # narrow cards look doubled and uneven.
    draw.line((*outer, outer[0]), fill=(238, 239, 243, 255), width=5, joint="curve")

    # Dotted caps and a translucent diamond footer mirror the in-game result
    # cards while leaving out Golden Thread and reward icons.
    for dot_row in range(3):
        row_y = y + 12 + dot_row * 8
        dot_count = 3 + dot_row * 2
        first_x = x + width // 2 - (dot_count - 1) * 5
        for dot in range(dot_count):
            dot_x = first_x + dot * 10
            draw.ellipse((dot_x - 2, row_y - 2, dot_x + 2, row_y + 2), fill=(255, 255, 255, 205))
    draw.polygon(
        (
            (x + 10, y + height - 48),
            (x + width - 10, y + height - 48),
            (x + width - 7, y + height - 35),
            (x + width // 2, y + height - 3),
            (x + 7, y + height - 35),
        ),
        fill=(245, 244, 248, 188),
    )
    _draw_stars(draw, x, y + height - 43, width, rarity)

    if pull.is_new:
        font = _font(13, bold=True)
        label = "NEW"
        bbox = draw.textbbox((0, 0), label, font=font)
        label_width = bbox[2] - bbox[0] + 12
        draw.rounded_rectangle(
            (x + width - label_width - 3, y + 29, x + width - 3, y + 50),
            radius=3,
            fill=(251, 206, 47, 245),
        )
        draw.text((x + width - label_width + 3, y + 30), label, font=font, fill=(111, 77, 15, 255))
    if pull.guaranteed_four_star or pull.guaranteed_five_star:
        font = _font(10, bold=True)
        label = "GUARANTEE ★5" if pull.guaranteed_five_star else "GUARANTEE ★4+"
        bbox = draw.textbbox((0, 0), label, font=font)
        label_width = bbox[2] - bbox[0] + 10
        draw.rounded_rectangle(
            (x + (width - label_width) // 2, y - 19, x + (width + label_width) // 2, y + 1),
            radius=4,
            fill=(159, 53, 188, 240),
        )
        draw.text(
            (x + (width - (bbox[2] - bbox[0])) // 2, y - 17),
            label,
            font=font,
            fill=(255, 255, 255, 255),
        )


def render_brown_dust_2_result(
    pulls: Sequence[BrownDust2Pull],
    image_data: Mapping[str, bytes],
) -> bytes:
    pulls = tuple(pulls)
    if len(pulls) not in {1, 10}:
        raise ValueError("Brown Dust 2 renderer chỉ hỗ trợ 1 hoặc 10 kết quả.")
    def raw_for(pull: BrownDust2Pull) -> bytes:
        return image_data.get(pull.costume.image_url, b"") or image_data.get(
            pull.costume.fallback_image_url, b""
        )

    featured_pull = max(pulls, key=lambda pull: pull.costume.rarity)
    canvas = _pearl_background(raw_for(featured_pull))
    if len(pulls) == 1:
        width, height = 250, 420
        positions = [((CANVAS_SIZE[0] - width) // 2, 142)]
    else:
        width, height = 98, 296
        gap = 13
        total_width = len(pulls) * width + (len(pulls) - 1) * gap
        start_x = (CANVAS_SIZE[0] - total_width) // 2
        positions = [(start_x + index * (width + gap), 178) for index in range(10)]
    for pull, (x, y) in zip(pulls, positions):
        raw = raw_for(pull)
        _draw_card(canvas, pull, raw, x=x, y=y, width=width, height=height)
    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


class BrownDust2GachaService:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self._pool_cache: tuple[float, BrownDust2Pool] | None = None
        self._pool_lock = asyncio.Lock()
        self._download_semaphore = asyncio.Semaphore(10)
        self.art_cache_dir: Path | None = None
        self.data_cache_dir: Path | None = None

    async def open(self) -> None:
        self.art_cache_dir = await asyncio.to_thread(
            _prepare_cache_dir, ART_CACHE_DIR, "Brown Dust 2 art cache"
        )
        self.data_cache_dir = await asyncio.to_thread(
            _prepare_cache_dir, DATA_CACHE_DIR, "Brown Dust 2 data cache"
        )
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60, connect=15, sock_read=45),
            headers={"User-Agent": "PetoDiscordBot/1.0 (Brown Dust 2 gacha wiki sync)"},
        )

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        self.session = None

    async def _cargo_rows(self, table: str, fields: str) -> list[Mapping[str, object]]:
        if not self.session:
            raise RuntimeError("Brown Dust 2 gacha chưa khởi tạo HTTP session.")
        rows: list[Mapping[str, object]] = []
        offset = 0
        while True:
            params = {
                "action": "cargoquery",
                "tables": table,
                "fields": fields,
                "limit": "500",
                "offset": str(offset),
                "format": "json",
            }
            async with self.session.get(API_URL, params=params) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
            batch = payload.get("cargoquery", []) if isinstance(payload, Mapping) else []
            batch = [row for row in batch if isinstance(row, Mapping)]
            rows.extend(batch)
            if len(batch) < 500:
                return rows
            offset += len(batch)

    async def _load_rows(self) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
        cache_path = self.data_cache_dir / "pool.json" if self.data_cache_dir else None
        try:
            costumes, companions = await asyncio.gather(
                self._cargo_rows(
                    "Costume",
                    "_pageName=Page,id,name,charName,isLimited,isDrawable,releaseDate",
                ),
                self._cargo_rows(
                    "Companion", "_pageName=Page,name,star,defaultCostumeId"
                ),
            )
            if cache_path:
                encoded = json.dumps(
                    {"costumes": costumes, "companions": companions},
                    ensure_ascii=False,
                )
                try:
                    await asyncio.to_thread(cache_path.write_text, encoded, encoding="utf-8")
                except OSError as error:
                    logger.warning("Không ghi được Brown Dust 2 data cache: %s", error)
            return costumes, companions
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as error:
            if cache_path:
                try:
                    cached = json.loads(
                        await asyncio.to_thread(cache_path.read_text, encoding="utf-8")
                    )
                    costumes = cached.get("costumes", [])
                    companions = cached.get("companions", [])
                    if isinstance(costumes, list) and isinstance(companions, list):
                        logger.warning("Brown Dust 2 Wiki tạm lỗi; dùng cache: %s", error)
                        return costumes, companions
                except (OSError, ValueError, AttributeError):
                    pass
            raise

    async def get_pool(self) -> BrownDust2Pool:
        now = time.monotonic()
        if self._pool_cache and now - self._pool_cache[0] < DATA_CACHE_SECONDS:
            return self._pool_cache[1]
        async with self._pool_lock:
            now = time.monotonic()
            if self._pool_cache and now - self._pool_cache[0] < DATA_CACHE_SECONDS:
                return self._pool_cache[1]
            costumes, companions = await self._load_rows()
            pool = parse_brown_dust_2_pool(costumes, companions)
            self._pool_cache = (now, pool)
            logger.info(
                "Brown Dust 2 pool: 5★=%s, 4★=%s, 3★=%s",
                len(pool.costumes(5)),
                len(pool.costumes(4)),
                len(pool.costumes(3)),
            )
            return pool

    async def _download_one(self, url: str) -> bytes:
        if not self.session or not url:
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

    async def _download_costume(self, costume: BrownDust2Costume) -> tuple[str, bytes, str, bytes]:
        primary = await self._download_one(costume.image_url)
        fallback = b"" if primary else await self._download_one(costume.fallback_image_url)
        return costume.image_url, primary, costume.fallback_image_url, fallback

    async def make_payload(
        self,
        pool: BrownDust2Pool,
        pulls: Sequence[BrownDust2Pull],
        *,
        pity: BrownDust2Pity,
        account_balance: int | None = None,
        point_cost: int = 0,
    ) -> BrownDust2Payload:
        unique = {pull.costume.costume_id: pull.costume for pull in pulls}
        downloaded = await asyncio.gather(
            *(self._download_costume(costume) for costume in unique.values())
        )
        image_data: dict[str, bytes] = {}
        for primary_url, primary, fallback_url, fallback in downloaded:
            image_data[primary_url] = primary
            image_data[fallback_url] = fallback
        png = await asyncio.to_thread(render_brown_dust_2_result, pulls, image_data)
        filename = f"brown-dust-2-gacha-{int(time.time() * 1000)}.png"
        file = discord.File(io.BytesIO(png), filename=filename)
        counts = {3: 0, 4: 0, 5: 0}
        lines: list[str] = []
        for index, pull in enumerate(pulls, start=1):
            counts[pull.costume.rarity] += 1
            new = " 🆕" if pull.is_new else ""
            lines.append(
                f"`{index:02d}` {'★' * pull.costume.rarity} "
                f"**[{discord.utils.escape_markdown(pull.costume.name)}]({pull.costume.page_url})**{new}"
            )
        embed = discord.Embed(
            title=f"✨ Brown Dust 2 Costume Draw — {len(pulls)} lượt",
            description="\n".join(lines),
            color=0xC783E8,
        )
        embed.add_field(
            name="Tổng kết",
            value=f"5★ `{counts[5]}` • 4★ `{counts[4]}` • 3★ `{counts[3]}`",
            inline=False,
        )
        embed.add_field(
            name="Bảo hiểm",
            value=(
                f"4★+ sau `{pity.since_four_star}/9` lượt chưa trúng • "
                f"5★ sau `{pity.since_five_star}/99` lượt chưa trúng"
            ),
            inline=False,
        )
        if account_balance is not None:
            embed.add_field(
                name="Peto Economy",
                value=(
                    f"Đã dùng `{point_cost:,}` điểm • Còn `{account_balance:,}` • "
                    "Kết quả đã lưu vào collection"
                ),
                inline=False,
            )
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(
            text="Pool thường tổng hợp • Không gồm Costume limited • Dữ liệu/ảnh: Brown Dust 2 Wiki"
        )
        return BrownDust2Payload(embed, file, tuple(pulls), pool)


def brown_dust_2_rates_embed(pool: BrownDust2Pool) -> discord.Embed:
    embed = discord.Embed(
        title="📊 Tỷ lệ Brown Dust 2 Costume Draw",
        description=(
            "• Costume 5★: `3%`\n"
            "• Costume 4★: `14%`\n"
            "• Costume 3★: `83%`\n\n"
            "Không có 4★+ trong 9 lượt thì lượt 10 bảo đảm **4★ trở lên**.\n"
            "Không có 5★ trong 99 lượt thì lượt 100 bảo đảm **5★**."
        ),
        color=0xC783E8,
    )
    embed.add_field(
        name="Pool thường",
        value=(
            f"5★ `{len(pool.costumes(5))}` • "
            f"4★ `{len(pool.costumes(4))}` • "
            f"3★ `{len(pool.costumes(3))}`"
        ),
        inline=False,
    )
    embed.set_footer(text="Costume drawable, không limited • Wiki cache làm mới mỗi 6 giờ")
    return embed
