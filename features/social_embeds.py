"""Custom social embeds cho Pixiv, X/Twitter và Instagram."""

from __future__ import annotations

import asyncio
import html
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands


logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
PIXIV_ARTWORK_RE = re.compile(
    r"^/(?:[a-z]{2}/)?artworks/(?P<id>\d+)(?:/\d+)?/?$",
    re.IGNORECASE,
)
X_STATUS_RE = re.compile(
    r"^/(?P<user>[^/]+)/status/(?P<id>\d+)(?:/(?:photo|video)/\d+)?/?$",
    re.IGNORECASE,
)
INSTAGRAM_POST_RE = re.compile(
    r"^/(?P<kind>p|reel|reels)/(?P<id>[^/]+)(?:/[^?#\s]*)?/?$",
    re.IGNORECASE,
)
IGNORE_MARKERS = ("fxignore", "peto-noembed")
MAX_SOCIAL_LINKS = 4
MAX_PIXIV_IMAGES = 5
PIXIV_BLUE = 0x0096FA
TWITTER_BLUE = 0x1DA1F2


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


UGOIRA_MAX_SOURCE_BYTES = (
    _env_int("PIXIV_UGOIRA_MAX_SOURCE_MIB", 256, 16, 1024) * 1024 * 1024
)
UGOIRA_MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
UGOIRA_MAX_FRAMES = 10_000
UGOIRA_FFMPEG_TIMEOUT = _env_int(
    "PIXIV_UGOIRA_FFMPEG_TIMEOUT_SECONDS", 180, 30, 600
)


@dataclass(frozen=True, slots=True)
class SocialLink:
    platform: str
    original: str
    external_id: str
    username: str = ""
    fixed: str = ""


class PixivUgoiraError(RuntimeError):
    pass


def _ffmpeg_executable() -> str:
    configured = os.getenv("FFMPEG_BINARY", "").strip()
    if configured:
        return configured
    detected = shutil.which("ffmpeg")
    if detected:
        return detected
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA", "").strip()
        if local_app_data:
            package_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
            candidates = sorted(
                package_root.glob("Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"),
                reverse=True,
            )
            if candidates:
                return str(candidates[0])
    return "ffmpeg"


class _PixivDescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts)).replace("\r", "")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def _clean_url_token(value: str) -> str:
    return str(value or "").rstrip(".,!?;:)]}>'\"")


def _normalized_host(parts: urllib.parse.SplitResult) -> str:
    host = (parts.hostname or "").casefold().rstrip(".")
    for prefix in ("www.", "mobile.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return host


def _rewritten_url(parts: urllib.parse.SplitResult, host: str) -> str:
    return urllib.parse.urlunsplit(("https", host, parts.path, "", ""))


def parse_social_url(url: str) -> SocialLink | None:
    """Chỉ nhận URL bài đăng cụ thể, không nhận profile hoặc trang chủ."""
    original = _clean_url_token(url)
    try:
        parts = urllib.parse.urlsplit(original)
    except ValueError:
        return None
    if parts.scheme.casefold() not in {"http", "https"}:
        return None

    host = _normalized_host(parts)
    path = parts.path or "/"

    if host == "pixiv.net":
        match = PIXIV_ARTWORK_RE.fullmatch(path)
        artwork_id = match.group("id") if match else ""
        if not artwork_id and path.casefold().endswith("/member_illust.php"):
            artwork_id = str(
                (urllib.parse.parse_qs(parts.query).get("illust_id") or [""])[0]
            )
        if artwork_id.isdigit():
            return SocialLink("Pixiv", original, artwork_id)

    if host in {"x.com", "twitter.com"}:
        match = X_STATUS_RE.fullmatch(path)
        if match:
            user = match.group("user")
            return SocialLink(
                "X / Twitter",
                original,
                match.group("id"),
                username=user,
                fixed=_rewritten_url(parts, "fxtwitter.com"),
            )

    if host == "instagram.com":
        match = INSTAGRAM_POST_RE.fullmatch(path)
        if match:
            # SaucyBot v2 hiện dùng vxinstagram, không phải kkinstagram.
            return SocialLink(
                "Instagram",
                original,
                match.group("id"),
                fixed=_rewritten_url(parts, "vxinstagram.com"),
            )
    return None


def social_links_from_text(text: str) -> list[SocialLink]:
    folded = str(text or "").casefold()
    if any(marker in folded for marker in IGNORE_MARKERS):
        return []

    found: list[SocialLink] = []
    seen: set[tuple[str, str]] = set()
    for match in URL_RE.finditer(str(text or "")):
        item = parse_social_url(match.group(0))
        if item is None:
            continue
        key = (item.platform, item.external_id)
        if key in seen:
            continue
        seen.add(key)
        found.append(item)
        if len(found) >= MAX_SOCIAL_LINKS:
            break
    return found


def _short_description(raw_html: str, limit: int = 3500) -> str:
    parser = _PixivDescriptionParser()
    try:
        parser.feed(str(raw_html or ""))
        value = parser.text()
    except Exception:
        value = re.sub(r"<[^>]+>", "", str(raw_html or ""))
        value = html.unescape(value).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _number(value) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _discord_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _message_upload_limit(message: discord.Message) -> int:
    limit = 10 * 1024 * 1024
    if message.guild is not None:
        limit = int(message.guild.filesize_limit)
    return max(1024 * 1024, limit - 64 * 1024)


def _prepare_ugoira_frames(
    archive_path: Path,
    frames: list[dict],
    workdir: Path,
) -> tuple[Path, float]:
    if not frames:
        raise PixivUgoiraError("Pixiv không trả danh sách frame Ugoira.")
    if len(frames) > UGOIRA_MAX_FRAMES:
        raise PixivUgoiraError(f"Ugoira có quá nhiều frame ({len(frames):,}).")

    concat_lines = ["ffconcat version 1.0"]
    duration_seconds = 0.0
    with zipfile.ZipFile(archive_path) as archive:
        members = {info.filename: info for info in archive.infolist() if not info.is_dir()}
        requested: list[tuple[dict, zipfile.ZipInfo]] = []
        extracted_size = 0
        for frame in frames:
            source_name = str(frame.get("file") or "")
            info = members.get(source_name)
            if info is None:
                raise PixivUgoiraError(f"Thiếu frame {source_name!r} trong ZIP Ugoira.")
            extracted_size += int(info.file_size)
            if extracted_size > UGOIRA_MAX_EXTRACTED_BYTES:
                raise PixivUgoiraError("Ugoira giải nén vượt giới hạn an toàn 1 GiB.")
            requested.append((frame, info))

        last_name = ""
        for index, (frame, info) in enumerate(requested):
            suffix = Path(info.filename).suffix.casefold()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                suffix = ".jpg"
            target_name = f"frame_{index:06d}{suffix}"
            target_path = workdir / target_name
            with archive.open(info) as source, target_path.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)

            try:
                delay_ms = max(1, int(frame.get("delay") or 60))
            except (TypeError, ValueError):
                delay_ms = 60
            duration = delay_ms / 1000.0
            duration_seconds += duration
            concat_lines.append(f"file '{target_name}'")
            concat_lines.append(f"duration {duration:.6f}")
            last_name = target_name

    # concat demuxer cần lặp lại frame cuối để duration cuối cùng được áp dụng.
    concat_lines.append(f"file '{last_name}'")
    concat_path = workdir / "frames.ffconcat"
    concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
    return concat_path, max(0.1, duration_seconds)


def _render_ugoira_video(
    concat_path: Path,
    output_path: Path,
    *,
    bitrate_kbps: int,
    max_width: int | None = None,
) -> None:
    filters = []
    if max_width:
        filters.append(f"scale='min({max_width},iw)':-2")
    filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")
    command = [
        _ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-an",
        "-vf",
        ",".join(filters),
        "-fps_mode",
        "vfr",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        f"{bitrate_kbps}k",
        "-maxrate",
        f"{bitrate_kbps}k",
        "-bufsize",
        f"{bitrate_kbps * 2}k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=concat_path.parent,
            capture_output=True,
            text=True,
            timeout=UGOIRA_FFMPEG_TIMEOUT,
        )
    except FileNotFoundError as error:
        raise PixivUgoiraError("Không tìm thấy FFmpeg trong PATH.") from error
    except subprocess.TimeoutExpired as error:
        raise PixivUgoiraError("FFmpeg chuyển Ugoira quá thời gian cho phép.") from error
    if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
        detail = (result.stderr or "FFmpeg không tạo được MP4.").strip()[-800:]
        raise PixivUgoiraError(detail)


def _pixiv_ugoira_view(
    details: dict,
    item: SocialLink,
    filename: str,
) -> discord.ui.LayoutView:
    title = discord.utils.escape_markdown(
        str(details.get("title") or f"Pixiv #{item.external_id}")
    )
    author = discord.utils.escape_markdown(
        str(details.get("userName") or details.get("userAccount") or "Pixiv")
    )
    user_id = str(details.get("userId") or "")
    artwork_url = f"https://www.pixiv.net/en/artworks/{item.external_id}"
    author_url = f"https://www.pixiv.net/en/users/{user_id}" if user_id else artwork_url
    description = _short_description(str(details.get("description") or ""), limit=3000)
    stats = (
        f"{_number(details.get('likeCount'))} 🙂    "
        f"{_number(details.get('bookmarkCount'))} ❤️    "
        f"{_number(details.get('viewCount'))} 👀"
    )
    created = _discord_timestamp(str(details.get("createDate") or ""))
    posted = f"-# Posted: <t:{int(created.timestamp())}:F>" if created else "-# Pixiv Ugoira"

    children: list[discord.ui.Item] = [
        discord.ui.TextDisplay(f"### [{title}]({artwork_url})"),
        discord.ui.TextDisplay(f"👤 [{author}]({author_url})"),
        discord.ui.Separator(),
    ]
    if str(details.get("aiType") or "") == "2":
        children.append(discord.ui.TextDisplay("🤖 AI Generated"))
    if description:
        children.append(discord.ui.TextDisplay(description))
    children.extend(
        (
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    media=f"attachment://{filename}",
                    description=str(details.get("title") or "Pixiv Ugoira")[:1024],
                )
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(stats),
            discord.ui.TextDisplay(posted),
        )
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_color=PIXIV_BLUE))
    return view


class SocialEmbeds(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.ugoira_semaphore = asyncio.Semaphore(1)
        cookie = os.getenv("PIXIV_PHPSESSID", "").strip()
        self.pixiv_cookie = cookie.removeprefix("PHPSESSID=").strip()

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"User-Agent": "Mozilla/5.0 (Peto Discord Bot social embed)"},
        )
        if not self.pixiv_cookie:
            logger.warning(
                "Thiếu PIXIV_PHPSESSID: custom Pixiv embed tạm tắt; X/Instagram vẫn hoạt động"
            )

    async def cog_unload(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()

    def _pixiv_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Referer": "https://www.pixiv.net/",
        }
        if self.pixiv_cookie:
            headers["Cookie"] = f"PHPSESSID={self.pixiv_cookie}"
        return headers

    async def _json(self, url: str, *, pixiv: bool = False) -> dict | None:
        if self.session is None:
            return None
        headers = self._pixiv_headers() if pixiv else None
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status != 200:
                    logger.info("Social API HTTP %s: %s", response.status, url)
                    return None
                payload = await response.json(content_type=None)
                return payload if isinstance(payload, dict) else None
        except (aiohttp.ClientError, TimeoutError, ValueError):
            logger.exception("Không đọc được social API: %s", url)
            return None

    async def _pixiv_image(
        self,
        urls: list[str],
        *,
        limit: int,
        artwork_id: str,
        index: int,
    ) -> tuple[io.BytesIO, str] | None:
        if self.session is None:
            return None
        for url in urls:
            if not url:
                continue
            try:
                async with self.session.get(url, headers=self._pixiv_headers()) as response:
                    if response.status != 200:
                        continue
                    length = int(response.headers.get("Content-Length") or 0)
                    if length and length > limit:
                        continue
                    data = await response.read()
                    if not data or len(data) > limit:
                        continue
                    content_type = str(response.headers.get("Content-Type") or "")
                    suffix = ".png" if "png" in content_type else ".jpg"
                    return io.BytesIO(data), f"pixiv_{artwork_id}_p{index}{suffix}"
            except (aiohttp.ClientError, TimeoutError, ValueError):
                continue
        return None

    async def _download_pixiv_archive(
        self,
        url: str,
        destination: Path,
    ) -> None:
        if self.session is None:
            raise PixivUgoiraError("HTTP session chưa sẵn sàng.")
        try:
            async with self.session.get(
                url,
                headers=self._pixiv_headers(),
                timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                if response.status != 200:
                    raise PixivUgoiraError(
                        f"Pixiv trả HTTP {response.status} khi tải ZIP Ugoira."
                    )
                length = int(response.headers.get("Content-Length") or 0)
                if length and length > UGOIRA_MAX_SOURCE_BYTES:
                    raise PixivUgoiraError("ZIP Ugoira vượt giới hạn tải an toàn.")
                total = 0
                with destination.open("wb") as output:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        total += len(chunk)
                        if total > UGOIRA_MAX_SOURCE_BYTES:
                            raise PixivUgoiraError("ZIP Ugoira vượt giới hạn tải an toàn.")
                        output.write(chunk)
                if total <= 0:
                    raise PixivUgoiraError("ZIP Ugoira tải về bị rỗng.")
        except PixivUgoiraError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as error:
            raise PixivUgoiraError("Không tải được ZIP Ugoira từ Pixiv.") from error

    async def _send_pixiv_ugoira(
        self,
        message: discord.Message,
        item: SocialLink,
        details: dict,
    ) -> bool:
        metadata_payload = await self._json(
            f"https://www.pixiv.net/ajax/illust/{item.external_id}/ugoira_meta",
            pixiv=True,
        )
        metadata = (metadata_payload or {}).get("body")
        if not isinstance(metadata, dict):
            logger.warning("Pixiv thiếu ugoira_meta artwork=%s", item.external_id)
            return False
        archive_url = str(metadata.get("originalSrc") or metadata.get("src") or "")
        frames = metadata.get("frames")
        if not archive_url or not isinstance(frames, list):
            logger.warning("Pixiv ugoira_meta không đầy đủ artwork=%s", item.external_id)
            return False

        upload_limit = _message_upload_limit(message)
        try:
            async with self.ugoira_semaphore:
                with tempfile.TemporaryDirectory(prefix=f"peto-ugoira-{item.external_id}-") as raw_dir:
                    workdir = Path(raw_dir)
                    archive_path = workdir / "frames.zip"
                    output_path = workdir / "ugoira.mp4"
                    await self._download_pixiv_archive(archive_url, archive_path)
                    concat_path, duration = await asyncio.to_thread(
                        _prepare_ugoira_frames,
                        archive_path,
                        frames,
                        workdir,
                    )

                    bitrate = min(
                        2000,
                        max(220, int(upload_limit * 8 * 0.80 / duration / 1000)),
                    )
                    await asyncio.to_thread(
                        _render_ugoira_video,
                        concat_path,
                        output_path,
                        bitrate_kbps=bitrate,
                    )
                    output_size = output_path.stat().st_size
                    if output_size > upload_limit:
                        retry_bitrate = max(
                            180,
                            int(bitrate * upload_limit / output_size * 0.72),
                        )
                        await asyncio.to_thread(
                            _render_ugoira_video,
                            concat_path,
                            output_path,
                            bitrate_kbps=retry_bitrate,
                            max_width=1280,
                        )
                        output_size = output_path.stat().st_size
                    if output_size > upload_limit:
                        raise PixivUgoiraError(
                            f"MP4 Ugoira {_number(output_size)} byte vượt giới hạn Discord."
                        )

                    filename = f"pixiv_{item.external_id}_ugoira.mp4"
                    file = discord.File(output_path, filename=filename)
                    view = _pixiv_ugoira_view(details, item, filename)
                    try:
                        await message.reply(
                            file=file,
                            view=view,
                            mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none(),
                            silent=True,
                        )
                    finally:
                        file.close()
            logger.info(
                "Đã chuyển Ugoira artwork=%s thành MP4 (%s frame, %.2fs, %s byte)",
                item.external_id,
                len(frames),
                duration,
                _number(output_size),
            )
            return True
        except PixivUgoiraError as error:
            logger.warning(
                "Không chuyển được Ugoira artwork=%s; dùng ảnh tĩnh: %s",
                item.external_id,
                error,
            )
            return False
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Không gửi được Ugoira artwork=%s", item.external_id)
            return False
        except (OSError, zipfile.BadZipFile, KeyError, RuntimeError, ValueError):
            logger.exception(
                "Pipeline Ugoira lỗi artwork=%s; dùng ảnh tĩnh",
                item.external_id,
            )
            return False

    async def _send_pixiv(self, message: discord.Message, item: SocialLink) -> bool:
        if not self.pixiv_cookie:
            return False
        details_payload = await self._json(
            f"https://www.pixiv.net/ajax/illust/{item.external_id}",
            pixiv=True,
        )
        if not details_payload or details_payload.get("error"):
            logger.info("Pixiv không trả details cho artwork=%s", item.external_id)
            return False
        details = details_payload.get("body")
        if not isinstance(details, dict):
            return False

        if str(details.get("illustType") or "") == "2":
            if await self._send_pixiv_ugoira(message, item, details):
                return True
            logger.info("Fallback ảnh preview cho Ugoira artwork=%s", item.external_id)

        page_count = max(1, int(details.get("pageCount") or 1))
        image_sets: list[list[str]] = []
        if page_count > 1:
            pages_payload = await self._json(
                f"https://www.pixiv.net/ajax/illust/{item.external_id}/pages",
                pixiv=True,
            )
            pages = (pages_payload or {}).get("body")
            if isinstance(pages, list):
                for page in pages[:MAX_PIXIV_IMAGES]:
                    urls = page.get("urls") if isinstance(page, dict) else None
                    if isinstance(urls, dict):
                        image_sets.append(
                            [str(urls.get("regular") or ""), str(urls.get("small") or "")]
                        )
        if not image_sets:
            urls = details.get("urls")
            if isinstance(urls, dict):
                image_sets.append(
                    [str(urls.get("regular") or ""), str(urls.get("small") or "")]
                )
        if not image_sets:
            return False

        upload_limit = _message_upload_limit(message)

        downloaded = []
        for index, urls in enumerate(image_sets):
            image = await self._pixiv_image(
                urls,
                limit=upload_limit,
                artwork_id=item.external_id,
                index=index,
            )
            if image is not None:
                downloaded.append(image)
        if not downloaded:
            return False

        files = [discord.File(stream, filename=name) for stream, name in downloaded]
        title = str(details.get("title") or f"Pixiv #{item.external_id}")[:256]
        author = str(details.get("userName") or details.get("userAccount") or "Pixiv")
        user_id = str(details.get("userId") or "")
        description = _short_description(str(details.get("description") or ""))
        if str(details.get("aiType") or "") == "2":
            description = ("🤖 **AI Generated**\n" + description).strip()

        embeds: list[discord.Embed] = []
        first = discord.Embed(
            title=title,
            url=f"https://www.pixiv.net/en/artworks/{item.external_id}",
            description=description or None,
            color=PIXIV_BLUE,
            timestamp=_discord_timestamp(str(details.get("createDate") or "")),
        )
        first.set_author(
            name=author[:256],
            url=f"https://www.pixiv.net/en/users/{user_id}" if user_id else None,
        )
        first.add_field(
            name="Thống kê",
            value=(
                f"{_number(details.get('likeCount'))} 🙂   "
                f"{_number(details.get('bookmarkCount'))} ❤️   "
                f"{_number(details.get('viewCount'))} 👀"
            ),
            inline=False,
        )
        first.set_image(url=f"attachment://{files[0].filename}")
        shown = len(files)
        footer = "Pixiv"
        if page_count > shown:
            footer += f" • Hiển thị {shown}/{page_count} ảnh"
        first.set_footer(text=footer)
        embeds.append(first)

        for file in files[1:]:
            extra = discord.Embed(
                url=f"https://www.pixiv.net/en/artworks/{item.external_id}",
                color=PIXIV_BLUE,
            )
            extra.set_image(url=f"attachment://{file.filename}")
            embeds.append(extra)

        try:
            await message.reply(
                embeds=embeds,
                files=files,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
                silent=True,
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Không gửi được Pixiv embed artwork=%s", item.external_id)
            return False

    async def _send_x(self, message: discord.Message, item: SocialLink) -> bool:
        payload = await self._json(
            f"https://api.fxtwitter.com/{item.username}/status/{item.external_id}"
        )
        tweet = (payload or {}).get("tweet")
        if not isinstance(tweet, dict):
            return await self._send_fixed_links(message, [item.fixed])

        media = tweet.get("media") if isinstance(tweet.get("media"), dict) else {}
        videos = media.get("videos") if isinstance(media.get("videos"), list) else []
        if videos:
            # Discord chỉ phát video inline khi nhận URL fixer; Embed API không có
            # trường để tự gắn video tùy ý.
            return await self._send_fixed_links(message, [item.fixed])

        author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
        screen_name = str(author.get("screen_name") or item.username)
        embed_base = {
            "url": str(tweet.get("url") or item.original),
            "color": TWITTER_BLUE,
        }
        photos = media.get("photos") if isinstance(media.get("photos"), list) else []
        embeds: list[discord.Embed] = []
        for index in range(max(1, min(len(photos), 4))):
            embed = discord.Embed(**embed_base)
            if index == 0:
                embed.description = str(tweet.get("text") or "")[:4096] or None
                embed.set_author(
                    name=f"{author.get('name') or screen_name} (@{screen_name})"[:256],
                    url=str(author.get("url") or f"https://x.com/{screen_name}"),
                    icon_url=str(author.get("avatar_url") or "") or None,
                )
                embed.add_field(name="Replies", value=_number(tweet.get("replies")), inline=True)
                embed.add_field(name="Reposts", value=_number(tweet.get("retweets")), inline=True)
                embed.add_field(name="Likes", value=_number(tweet.get("likes")), inline=True)
                embed.add_field(name="Views", value=_number(tweet.get("views")), inline=True)
                timestamp = tweet.get("created_timestamp")
                if timestamp:
                    try:
                        embed.timestamp = datetime.fromtimestamp(int(timestamp), timezone.utc)
                    except (TypeError, ValueError, OSError):
                        pass
                embed.set_footer(text="X / Twitter")
            if photos:
                photo = photos[index]
                if isinstance(photo, dict) and photo.get("url"):
                    embed.set_image(url=str(photo["url"]))
            embeds.append(embed)

        try:
            await message.reply(
                embeds=embeds,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
                silent=True,
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Không gửi được X embed status=%s", item.external_id)
            return False

    async def _send_fixed_links(self, message: discord.Message, urls: list[str]) -> bool:
        urls = [url for url in urls if url]
        if not urls:
            return False
        try:
            await message.reply(
                "\n".join(urls),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
                silent=True,
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Không gửi được fixer URL cho message=%s", message.id)
            return False

    async def _suppress_original(self, message: discord.Message) -> None:
        # Đặt flag sau khi gửi thành công giống SaucyBot. Không cần chờ native
        # embed xuất hiện; Discord sẽ giữ cờ suppress cho cả preview đến sau.
        try:
            await message.edit(suppress=True)
        except discord.Forbidden:
            logger.warning(
                "Không ẩn được embed gốc message=%s: bot cần quyền Manage Messages",
                message.id,
            )
        except discord.HTTPException:
            logger.exception("Không ẩn được embed gốc message=%s", message.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.webhook_id is not None or not message.content:
            return
        items = social_links_from_text(message.content)
        if not items:
            return

        processed = False
        instagram_urls: list[str] = []
        async with message.channel.typing():
            for item in items:
                if item.platform == "Pixiv":
                    processed = await self._send_pixiv(message, item) or processed
                elif item.platform == "X / Twitter":
                    processed = await self._send_x(message, item) or processed
                elif item.platform == "Instagram":
                    instagram_urls.append(item.fixed)
            if instagram_urls:
                processed = await self._send_fixed_links(message, instagram_urls) or processed

        if processed:
            await self._suppress_original(message)
            logger.info(
                "Đã cải thiện social embed message=%s platforms=%s",
                message.id,
                ",".join(item.platform for item in items),
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SocialEmbeds(bot))
