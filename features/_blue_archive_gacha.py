"""Blue Archive recruitment data, rolling and result rendering.

This is a helper module rather than a Discord extension.  ``features`` is loaded
automatically by :mod:`bot`, so the leading underscore intentionally keeps it
out of extension discovery; ``features.limbus_gacha`` owns the public /gacha
command and delegates Blue Archive requests here.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import random
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

import aiohttp
import discord
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps


logger = logging.getLogger(__name__)

GAME_ID = "blue_archive"
DATA_ROOT = "https://schaledb.com/data"
IMAGE_ROOT = "https://schaledb.com/images/student/icon"
REGION_INDEX = {"jp": 0, "global": 1, "cn": 2}
REGION_CONFIG_NAMES = {"jp": "Jp", "global": "Global", "cn": "Cn"}
REGION_LABELS = {"jp": "JP", "global": "Global", "cn": "CN"}
KIND_STAR1 = "ba1"
KIND_STAR2 = "ba2"
KIND_STAR3 = "ba3"
KIND_BY_STAR = {1: KIND_STAR1, 2: KIND_STAR2, 3: KIND_STAR3}

ART_CACHE_DIR = Path(
    os.getenv("BLUE_ARCHIVE_GACHA_ART_CACHE_DIR", "blue_archive_gacha_art_cache")
).resolve()
DATA_CACHE_SECONDS = 15 * 60
IMAGE_MAX_BYTES = 8 * 1024 * 1024
CANVAS_SIZE = (1334, 750)
UI_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "blue_archive_gacha"


@dataclass(frozen=True, slots=True)
class BlueArchiveStudent:
    student_id: int
    name: str
    star_grade: int
    limited_code: int
    school: str
    image_url: str

    @property
    def kind(self) -> str:
        return KIND_BY_STAR[self.star_grade]


@dataclass(frozen=True, slots=True)
class BlueArchiveBanner:
    region: str
    banner_id: str
    start_at: int
    end_at: int
    pickups: tuple[BlueArchiveStudent, ...]
    one_star: tuple[BlueArchiveStudent, ...]
    two_star: tuple[BlueArchiveStudent, ...]
    three_star: tuple[BlueArchiveStudent, ...]

    def target(self, value: str | None) -> BlueArchiveStudent:
        if not self.pickups:
            raise RuntimeError("Banner Blue Archive hiện tại không có học sinh pickup.")
        if not value:
            return self.pickups[0]
        needle = str(value).strip().casefold()
        for student in self.pickups:
            if needle in {str(student.student_id), student.name.casefold()}:
                return student
        raise ValueError("Học sinh mục tiêu không thuộc banner hiện tại.")


@dataclass(frozen=True, slots=True)
class BlueArchivePull:
    student: BlueArchiveStudent
    is_pickup: bool = False
    is_new: bool = False


@dataclass(frozen=True, slots=True)
class BlueArchivePayload:
    embed: discord.Embed
    file: discord.File
    pulls: tuple[BlueArchivePull, ...]
    banner: BlueArchiveBanner
    target: BlueArchiveStudent
    recruitment_points: int


def mark_new_blue_archive_pulls(
    pulls: Sequence[BlueArchivePull],
    owned_by_kind: Mapping[str, set[str]],
) -> tuple[BlueArchivePull, ...]:
    """Mark only the first newly collected copy of each student as ``NEW``."""

    seen = {
        (str(kind), str(name))
        for kind, names in owned_by_kind.items()
        for name in names
    }
    marked: list[BlueArchivePull] = []
    for pull in pulls:
        key = (pull.student.kind, pull.student.name)
        is_new = key not in seen
        seen.add(key)
        marked.append(
            BlueArchivePull(
                student=pull.student,
                is_pickup=pull.is_pickup,
                is_new=is_new,
            )
        )
    return tuple(marked)


def _region_value(value: str | None) -> str:
    normalized = str(value or "global").strip().casefold()
    if normalized not in REGION_INDEX:
        raise ValueError("Server Blue Archive phải là JP, Global hoặc CN.")
    return normalized


def _sequence_value(value: object, index: int, default: object) -> object:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value[index] if index < len(value) else default
    return value if value is not None else default


def _student_from_json(raw: Mapping[str, object], region_index: int) -> BlueArchiveStudent | None:
    try:
        student_id = int(raw.get("Id", 0))
        name = str(raw.get("Name") or raw.get("PathName") or student_id).strip()
        star_grade = int(raw.get("StarGrade", 0))
        released = bool(_sequence_value(raw.get("IsReleased"), region_index, False))
        limited_code = int(_sequence_value(raw.get("IsLimited"), region_index, 0) or 0)
    except (TypeError, ValueError):
        return None
    if not released or star_grade not in KIND_BY_STAR or not name or student_id <= 0:
        return None
    return BlueArchiveStudent(
        student_id=student_id,
        name=name,
        star_grade=star_grade,
        limited_code=limited_code,
        school=str(raw.get("School") or "Unknown"),
        image_url=f"{IMAGE_ROOT}/{student_id}.webp",
    )


def parse_blue_archive_banner(
    config: Mapping[str, object],
    students_payload: Mapping[str, Mapping[str, object]],
    region: str = "global",
) -> BlueArchiveBanner:
    """Build a current recruitable pool from SchaleDB's public payloads."""

    region = _region_value(region)
    region_index = REGION_INDEX[region]
    config_name = REGION_CONFIG_NAMES[region]
    regions = config.get("Regions")
    if not isinstance(regions, Sequence):
        raise RuntimeError("SchaleDB không trả danh sách region.")
    region_config = next(
        (
            item
            for item in regions
            if isinstance(item, Mapping) and str(item.get("Name")) == config_name
        ),
        None,
    )
    if not region_config:
        raise RuntimeError(f"SchaleDB không có cấu hình region {config_name}.")
    current = region_config.get("CurrentGacha")
    if not isinstance(current, Sequence) or not current or not isinstance(current[0], Mapping):
        raise RuntimeError(f"Hiện không có banner Blue Archive {REGION_LABELS[region]}.")
    gacha = current[0]
    pickup_ids = tuple(int(value) for value in (gacha.get("characters") or ()))

    students: dict[int, BlueArchiveStudent] = {}
    for raw in students_payload.values():
        if isinstance(raw, Mapping):
            student = _student_from_json(raw, region_index)
            if student:
                students[student.student_id] = student
    pickups = tuple(students[value] for value in pickup_ids if value in students)
    if not pickups:
        raise RuntimeError("Không tìm thấy artwork/dữ liệu pickup của banner hiện tại.")

    # Code 2 is an event/welfare unit.  Codes 0 and 4 are permanent pool
    # students; limited/fes students enter only while present in this banner.
    permanent = tuple(
        student
        for student in students.values()
        if student.limited_code in {0, 4}
    )
    current_limited = tuple(
        student for student in pickups if student.limited_code not in {0, 4}
    )
    three_star = tuple(
        dict.fromkeys(
            student.student_id
            for student in (*permanent, *current_limited)
            if student.star_grade == 3
        )
    )
    three_star_students = tuple(students[student_id] for student_id in three_star)
    one_star = tuple(student for student in permanent if student.star_grade == 1)
    two_star = tuple(student for student in permanent if student.star_grade == 2)
    if not one_star or not two_star or not three_star_students:
        raise RuntimeError("Pool Blue Archive không đầy đủ để quay.")

    start_at = int(gacha.get("start") or 0)
    end_at = int(gacha.get("end") or 0)
    banner_id = f"{region}:{start_at}:{end_at}"
    return BlueArchiveBanner(
        region=region,
        banner_id=banner_id,
        start_at=start_at,
        end_at=end_at,
        pickups=pickups,
        one_star=one_star,
        two_star=two_star,
        three_star=three_star_students,
    )


def pull_blue_archive(
    banner: BlueArchiveBanner,
    target: BlueArchiveStudent,
    count: int,
    rng: random.Random | random.SystemRandom | None = None,
) -> tuple[BlueArchivePull, ...]:
    """Roll 3.0% / 18.5% / 78.5%; the tenth slot is guaranteed 2★+."""

    if count not in {1, 10}:
        raise ValueError("Blue Archive chỉ hỗ trợ quay ×1 hoặc ×10.")
    rng = rng or random.SystemRandom()
    other_three = tuple(
        student for student in banner.three_star if student.student_id != target.student_id
    )
    results: list[BlueArchivePull] = []
    for position in range(count):
        value = rng.random()
        guaranteed = count == 10 and position == 9
        if value < 0.007:
            student = target
        elif value < 0.03:
            student = rng.choice(other_three or (target,))
        elif guaranteed or value < 0.215:
            student = rng.choice(banner.two_star)
        else:
            student = rng.choice(banner.one_star)
        results.append(
            BlueArchivePull(student, is_pickup=student.student_id == target.student_id)
        )
    return tuple(results)


def _prepare_cache_dir(path: Path) -> Path | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path if path.is_dir() else None
    except OSError as error:
        logger.warning("Không tạo được Blue Archive art cache tại %s: %s", path, error)
        return None


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path(os.environ.get("WINDIR", "C:/Windows"))
        / "Fonts"
        / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _student_icon(raw: bytes, size: tuple[int, int]) -> Image.Image:
    """Fit a SchaleDB icon without destroying its transparent background."""

    try:
        with Image.open(io.BytesIO(raw)) as source:
            icon = ImageOps.contain(
                source.convert("RGBA"), size, method=Image.Resampling.LANCZOS
            )
        fitted = Image.new("RGBA", size, (0, 0, 0, 0))
        fitted.alpha_composite(
            icon,
            ((size[0] - icon.width) // 2, (size[1] - icon.height) // 2),
        )
        return fitted
    except Exception:
        fallback = Image.new("RGBA", size, (204, 225, 238, 255))
        draw = ImageDraw.Draw(fallback, "RGBA")
        draw.ellipse(
            (size[0] // 3, size[1] // 4, size[0] * 2 // 3, size[1] * 3 // 4),
            fill=(121, 164, 193, 255),
        )
        return fallback


@lru_cache(maxsize=8)
def _ui_asset(filename: str) -> Image.Image:
    """Load one packaged recruitment UI asset without keeping a file handle open."""

    with Image.open(UI_ASSET_DIR / filename) as source:
        return source.convert("RGBA").copy()


def _card_polygon(
    x: int,
    y: int,
    width: int,
    height: int,
    skew: int,
) -> tuple[tuple[int, int], ...]:
    return (
        (x + skew, y),
        (x + width + skew, y),
        (x + width, y + height),
        (x, y + height),
    )


def _draw_rarity_glow(
    canvas: Image.Image,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    star_grade: int,
) -> None:
    if star_grade < 2:
        return

    # The source renderer stretches a bright column through both rows. That
    # overwhelms the cards on Discord, especially for 2-star pulls. Keep only a
    # restrained vertical rarity wash behind the card.
    color = (255, 242, 164) if star_grade == 2 else (248, 190, 232)
    max_alpha = 58 if star_grade == 2 else 86
    skew = max(10, round(height * 0.1763))
    footprint = width + skew
    glow_height = round(height * 1.55)
    glow_top = y - (glow_height - height) // 2
    glow_left = x - 15
    glow_right = x + footprint + 15
    glow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    for offset in range(glow_height):
        distance = abs((offset + 0.5) / glow_height - 0.5) * 2
        strength = max(0.0, 1.0 - distance) ** 2
        alpha = round(max_alpha * strength)
        if alpha:
            glow_draw.line(
                (glow_left, glow_top + offset, glow_right, glow_top + offset),
                fill=(*color, alpha),
                width=1,
            )
    canvas.alpha_composite(glow)


def _draw_student_card(
    canvas: Image.Image,
    pull: BlueArchivePull,
    raw_artwork: bytes,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
) -> None:
    """Draw the skewed result card used by the reference bot."""

    star_grade = pull.student.star_grade
    skew = max(10, round(height * 0.1763))
    footprint = width + skew
    polygon = _card_polygon(x, y, width, height, skew)

    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    shadow_polygon = tuple((px + 4, py + 6) for px, py in polygon)
    ImageDraw.Draw(shadow, "RGBA").polygon(shadow_polygon, fill=(26, 52, 70, 80))
    shadow = shadow.filter(ImageFilter.GaussianBlur(7))
    canvas.alpha_composite(shadow)

    card_layer = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    card_draw = ImageDraw.Draw(card_layer, "RGBA")
    fill = {
        1: (228, 235, 241, 255),
        2: (254, 246, 135, 255),
        3: (251, 197, 229, 255),
    }[star_grade]
    card_draw.polygon(polygon, fill=fill)

    card_mask = Image.new("L", CANVAS_SIZE, 0)
    ImageDraw.Draw(card_mask).polygon(polygon, fill=255)
    bar_height = max(40, round(height * 0.23))
    available_height = height - bar_height
    portrait_size = round(min(footprint * 0.88, available_height * 1.08))
    portrait = _student_icon(raw_artwork, (portrait_size, portrait_size))
    portrait_layer = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    portrait_x = x + (footprint - portrait_size) // 2
    portrait_y = y + (available_height - portrait_size) // 2
    portrait_layer.alpha_composite(portrait, (portrait_x, portrait_y))
    portrait_layer.putalpha(
        ImageChops.multiply(portrait_layer.getchannel("A"), card_mask)
    )
    card_layer.alpha_composite(portrait_layer)

    # The original result card uses a dark strip for rarity stars rather than
    # writing student names into the image; names remain readable in the embed.
    bar_top = y + height - bar_height
    bar_skew = max(5, round(bar_height * 0.1763))
    bar_polygon = (
        (x + bar_skew + 2, bar_top),
        (x + width + bar_skew - 2, bar_top),
        (x + width - 2, y + height - 2),
        (x + 2, y + height - 2),
    )
    card_draw.polygon(bar_polygon, fill=(102, 115, 134, 245))
    card_draw.line((*polygon, polygon[0]), fill=(255, 255, 255, 245), width=max(2, width // 65), joint="curve")
    canvas.alpha_composite(card_layer)

    star_asset = _ui_asset("Star.png")
    star_size = max(19, round(width * 0.13))
    star_asset = star_asset.resize((star_size, star_size), Image.Resampling.LANCZOS)
    gap = max(3, width // 32)
    stars_width = star_grade * star_size + (star_grade - 1) * gap
    bar_center = x + width // 2 + bar_skew // 2
    stars_x = bar_center - stars_width // 2
    stars_y = bar_top + (bar_height - star_size) // 2
    for index in range(star_grade):
        canvas.alpha_composite(star_asset, (stars_x + index * (star_size + gap), stars_y))

    if pull.is_new:
        new_width = max(52, round(width * 0.36))
        new_height = max(20, round(new_width * 23 / 55))
        new_asset = _ui_asset("New.png").resize(
            (new_width, new_height), Image.Resampling.LANCZOS
        )
        canvas.alpha_composite(new_asset, (x + skew + 4, y + 5))


def _draw_recruitment_points(canvas: Image.Image, recruitment_points: int) -> None:
    """Draw the bottom-right Recruitment Point widget from the reference UI."""

    draw = ImageDraw.Draw(canvas, "RGBA")
    panel_width, panel_height = 200, 60
    x = CANVAS_SIZE[0] - panel_width - 50
    y = CANVAS_SIZE[1] - panel_height - 20
    skew = 10
    upper = ((x + skew, y), (x + panel_width, y), (x + panel_width - skew, y + 30), (x, y + 30))
    lower = ((x, y + 30), (x + panel_width - skew, y + 30), (x + panel_width - skew * 2, y + panel_height), (x - skew, y + panel_height))
    draw.polygon(upper, fill=(255, 255, 255, 248), outline=(53, 145, 204, 255))
    draw.polygon(lower, fill=(44, 96, 140, 250), outline=(53, 145, 204, 255))
    draw.text((x + 42, y + 6), "Recruitment Point", font=_font(15, bold=True), fill=(42, 91, 130, 255))
    value = str(max(0, int(recruitment_points)))
    bbox = draw.textbbox((0, 0), value, font=_font(20, bold=True))
    draw.text(
        (x + panel_width - (bbox[2] - bbox[0]) - 18, y + 33),
        value,
        font=_font(20, bold=True),
        fill=(255, 255, 255, 255),
    )
    point_asset = _ui_asset("Point.png").resize((100, 80), Image.Resampling.LANCZOS)
    canvas.alpha_composite(point_asset, (x - 60, y - 10))


def render_blue_archive_result(
    pulls: Sequence[BlueArchivePull],
    image_data: Mapping[str, bytes],
    *,
    region_label: str,
    target_name: str,
    recruitment_points: int,
) -> bytes:
    """Render the result board using the layout and packaged UI of the reference bot."""

    canvas = _ui_asset("Background.png").resize(CANVAS_SIZE, Image.Resampling.LANCZOS)
    pulls = tuple(pulls)
    if len(pulls) == 1:
        columns = rows = 1
        card_width, card_height = 300, 375
        card_skew = round(card_height * 0.1763)
        gap_x = gap_y = 0
        start_x = (CANVAS_SIZE[0] - card_width - card_skew) // 2
        start_y = 145
    else:
        columns = 5
        rows = (len(pulls) + columns - 1) // columns
        card_width, card_height = 170, 210
        card_skew = round(card_height * 0.1763)
        gap_x, gap_y = 26, 56
        footprint = card_width + card_skew
        total_width = columns * footprint + (columns - 1) * gap_x
        total_height = rows * card_height + (rows - 1) * gap_y
        start_x = (CANVAS_SIZE[0] - total_width) // 2
        start_y = 92 if rows == 2 else (CANVAS_SIZE[1] - total_height) // 2

    positions = []
    for index, pull in enumerate(pulls):
        row, column = divmod(index, columns)
        x = start_x + column * (card_width + card_skew + gap_x)
        y = start_y + row * (card_height + gap_y)
        positions.append((x, y))
        _draw_rarity_glow(
            canvas,
            x=x,
            y=y,
            width=card_width,
            height=card_height,
            star_grade=pull.student.star_grade,
        )

    for pull, (x, y) in zip(pulls, positions):
        _draw_student_card(
            canvas,
            pull,
            image_data.get(pull.student.image_url, b""),
            x=x,
            y=y,
            width=card_width,
            height=card_height,
        )

    _draw_recruitment_points(canvas, recruitment_points)
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


class BlueArchiveGachaService:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self._banner_cache: dict[str, tuple[float, BlueArchiveBanner]] = {}
        self._banner_lock = asyncio.Lock()
        self._download_semaphore = asyncio.Semaphore(10)
        self.art_cache_dir: Path | None = None

    async def open(self) -> None:
        self.art_cache_dir = await asyncio.to_thread(_prepare_cache_dir, ART_CACHE_DIR)
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=35, connect=12, sock_read=25),
            headers={"User-Agent": "PetoDiscordBot/1.0 (Blue Archive gacha)"},
        )

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        self.session = None

    async def _fetch_json(self, url: str) -> Mapping[str, object]:
        if not self.session:
            raise RuntimeError("Blue Archive gacha chưa khởi tạo HTTP session.")
        async with self.session.get(url) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
        if not isinstance(payload, Mapping):
            raise RuntimeError("SchaleDB trả dữ liệu không hợp lệ.")
        return payload

    async def get_banner(self, region: str = "global") -> BlueArchiveBanner:
        region = _region_value(region)
        cached = self._banner_cache.get(region)
        now = time.monotonic()
        if cached and now - cached[0] < DATA_CACHE_SECONDS:
            return cached[1]
        async with self._banner_lock:
            cached = self._banner_cache.get(region)
            now = time.monotonic()
            if cached and now - cached[0] < DATA_CACHE_SECONDS:
                return cached[1]
            config, students = await asyncio.gather(
                self._fetch_json(f"{DATA_ROOT}/config.min.json"),
                self._fetch_json(f"{DATA_ROOT}/en/students.min.json"),
            )
            banner = parse_blue_archive_banner(config, students, region)
            self._banner_cache[region] = (now, banner)
            return banner

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
        banner: BlueArchiveBanner,
        target: BlueArchiveStudent,
        pulls: Sequence[BlueArchivePull],
        *,
        recruitment_points: int = 0,
        account_balance: int | None = None,
        point_cost: int = 0,
    ) -> BlueArchivePayload:
        urls = tuple(dict.fromkeys(pull.student.image_url for pull in pulls))
        downloaded = await asyncio.gather(*(self._download_image(url) for url in urls))
        image_data = dict(zip(urls, downloaded))
        png = await asyncio.to_thread(
            render_blue_archive_result,
            pulls,
            image_data,
            region_label=REGION_LABELS[banner.region],
            target_name=target.name,
            recruitment_points=recruitment_points,
        )
        filename = f"blue-archive-gacha-{int(time.time() * 1000)}.png"
        file = discord.File(io.BytesIO(png), filename=filename)
        counts = {1: 0, 2: 0, 3: 0}
        lines = []
        for index, pull in enumerate(pulls, start=1):
            counts[pull.student.star_grade] += 1
            pickup = " 🎯" if pull.is_pickup else ""
            lines.append(f"`{index:02d}` {'★' * pull.student.star_grade} **{discord.utils.escape_markdown(pull.student.name)}**{pickup}")
        embed = discord.Embed(
            title=f"🎓 Blue Archive Recruitment — {len(pulls)} lượt",
            description="\n".join(lines),
            color=0x68C7F2,
        )
        embed.add_field(
            name="Tổng kết",
            value=f"3★ `{counts[3]}` • 2★ `{counts[2]}` • 1★ `{counts[1]}`",
            inline=False,
        )
        embed.add_field(
            name="Banner",
            value=f"{REGION_LABELS[banner.region]} • Mục tiêu **{target.name}** • Recruitment Points `{recruitment_points}/200`",
            inline=False,
        )
        if account_balance is not None:
            embed.add_field(
                name="Peto Economy",
                value=f"Đã dùng `{point_cost:,}` điểm • Còn `{account_balance:,}` • Kết quả đã lưu vào collection",
                inline=False,
            )
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text="Tỷ lệ 3★ 3% • 2★ 18,5% • Lượt 10 bảo đảm 2★+")
        return BlueArchivePayload(embed, file, tuple(pulls), banner, target, recruitment_points)

def blue_archive_rates_embed(banner: BlueArchiveBanner, target: BlueArchiveStudent) -> discord.Embed:
    embed = discord.Embed(
        title="📊 Tỷ lệ Blue Archive Recruitment",
        description=(
            f"**Mục tiêu:** {target.name}\n"
            "• Pickup 3★: `0,7%`\n"
            "• 3★ khác: `2,3%`\n"
            "• 2★: `18,5%`\n"
            "• 1★: `78,5%`\n\n"
            "Lượt thứ 10 của quay ×10 luôn là **2★ trở lên**."
        ),
        color=0x68C7F2,
    )
    embed.set_footer(text=f"SchaleDB • {REGION_LABELS[banner.region]} • Pool tự làm mới mỗi 15 phút")
    return embed
