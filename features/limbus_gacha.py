"""Limbus Company Standard Extraction simulator.

The simulator deliberately reads the already-synced wiki database instead of
scraping the live website for every pull.  It does not spend currency or keep
an ownership inventory; the result is only for fun.
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
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


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

KIND_EGO = "ego"
KIND_ID3 = "id3"
KIND_ID2 = "id2"
KIND_ID1 = "id1"

# Standard Extraction rates while the account does not own every E.G.O.
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
RARITY_SHORT = {
    KIND_ID1: "1*",
    KIND_ID2: "2*",
    KIND_ID3: "3*",
    KIND_EGO: "E.G.O",
}
RARITY_COLOR = {
    KIND_ID1: (112, 119, 126),
    KIND_ID2: (224, 168, 55),
    KIND_ID3: (223, 64, 83),
    KIND_EGO: (143, 92, 224),
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
        rows = connection.execute(
            """
            SELECT p.title, p.url,
                   COALESCE(a.thumbnail_url, a.original_url, '') AS image_url
            FROM wiki_pages AS p
            LEFT JOIN wiki_assets AS a ON a.pageid = p.pageid
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
    used_egos: set[str] = set()
    for index in range(count):
        rates = TENTH_PULL_RATES if count == 10 and index == 9 else STANDARD_RATES
        kind = roll_kind(rng, rates)
        candidates = pool.entries(kind)
        if kind == KIND_EGO:
            remaining = tuple(item for item in candidates if item.name not in used_egos)
            candidates = remaining or candidates
        if not candidates:
            raise RuntimeError(f"Pool {kind} đang trống.")
        result = rng.choice(candidates)
        results.append(result)
        if result.kind == KIND_EGO:
            used_egos.add(result.name)
    return tuple(results)


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    custom = os.getenv("LIMBUS_GACHA_FONT_PATH", "").strip()
    candidates = [
        custom,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def render_gacha_collage(
    pulls: Sequence[GachaEntry],
    image_data: Mapping[str, bytes],
) -> bytes:
    """Render ten pulls as a compact 5x2 PNG; invalid images become placeholders."""
    if len(pulls) != 10:
        raise ValueError("Collage cần đúng 10 kết quả.")
    card_w, card_h, gap, margin = 202, 252, 10, 16
    canvas_w = margin * 2 + card_w * 5 + gap * 4
    canvas_h = margin * 2 + card_h * 2 + gap
    canvas = Image.new("RGB", (canvas_w, canvas_h), (18, 20, 26))
    draw = ImageDraw.Draw(canvas)
    label_font = _load_font(20, bold=True)

    for index, entry in enumerate(pulls):
        column, row = index % 5, index // 5
        x = margin + column * (card_w + gap)
        y = margin + row * (card_h + gap)
        color = RARITY_COLOR[entry.kind]
        draw.rounded_rectangle(
            (x, y, x + card_w, y + card_h), radius=12, fill=(35, 38, 48), outline=color, width=5
        )
        art_box = (x + 7, y + 7, x + card_w - 7, y + card_h - 48)
        raw = image_data.get(entry.image_url, b"")
        art: Image.Image | None = None
        if raw:
            try:
                with Image.open(io.BytesIO(raw)) as source:
                    source.load()
                    art = ImageOps.fit(
                        source.convert("RGB"),
                        (art_box[2] - art_box[0], art_box[3] - art_box[1]),
                        method=Image.Resampling.LANCZOS,
                    )
            except (OSError, UnidentifiedImageError, ValueError):
                art = None
        if art is not None:
            canvas.paste(art, art_box[:2])
        else:
            draw.rectangle(art_box, fill=(49, 52, 64))
            draw.text(
                ((art_box[0] + art_box[2]) // 2, (art_box[1] + art_box[3]) // 2),
                RARITY_SHORT[entry.kind],
                fill=color,
                font=label_font,
                anchor="mm",
            )
        label = f"#{index + 1:02d}  {RARITY_SHORT[entry.kind]}"
        draw.text(
            (x + card_w // 2, y + card_h - 24),
            label,
            fill=color,
            font=label_font,
            anchor="mm",
        )

    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
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
        text="Lượt 10 bảo đảm 2★ trở lên • Mô phỏng không tiêu Lunacy và không lưu sở hữu"
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
                payload = await self.cog.make_payload(count)
                attachments: list[discord.File] = [payload.file] if payload.file else []
                await interaction.edit_original_response(
                    embed=payload.embed,
                    attachments=attachments,
                    view=self,
                )
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


class LimbusGacha(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.pool: GachaPool | None = None
        self.pool_loaded_at = 0.0
        self.pool_lock = asyncio.Lock()
        self.image_semaphore = asyncio.Semaphore(10)
        self.art_cache_dir: Path | None = None

    async def cog_load(self) -> None:
        # Chuẩn bị filesystem trước khi mở HTTP session để không rò session nếu
        # đường dẫn cache bị một file khác chiếm chỗ hoặc thiếu quyền ghi.
        self.art_cache_dir = await asyncio.to_thread(
            _prepare_art_cache_dir, ART_CACHE_DIR
        )
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

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()
        self.session = None

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

    async def make_payload(self, count: int) -> GachaPayload:
        pool = await self.get_pool()
        pulls = pull_entries(pool, count)
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
                text="Peto Gacha • Mô phỏng, không tiêu Lunacy • Nguồn roster: Limbus Company Wiki"
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
        file = await self._collage_file(pulls)
        if file:
            embed.set_image(url=f"attachment://{file.filename}")
        embed.set_footer(
            text="Lượt 10 bảo đảm 2★+ • Mô phỏng, không tiêu Lunacy • Nguồn roster: Limbus Company Wiki"
        )
        return GachaPayload(embed, file, pulls)

    @app_commands.command(
        name="gacha",
        description="Mô phỏng Standard Extraction của Limbus Company",
    )
    @app_commands.describe(pulls="Số lượt quay; mặc định là 10")
    @app_commands.choices(
        pulls=[
            app_commands.Choice(name="Quay ×1", value=1),
            app_commands.Choice(name="Quay ×10", value=10),
        ]
    )
    async def gacha(
        self,
        interaction: discord.Interaction,
        pulls: app_commands.Choice[int] | None = None,
    ) -> None:
        count = pulls.value if pulls else 10
        await interaction.response.defer(thinking=True)
        try:
            payload = await self.make_payload(count)
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
