from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger(__name__)

PANEL_TIMEOUT = 10 * 60
MAX_MEDIA_DURATION = 10 * 60
DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024
UPLOAD_SAFETY_MARGIN = 128 * 1024
MAX_PARALLEL_DOWNLOADS = 2
MAX_PARALLEL_PROBES = 3
MAX_TIKTOK_IMAGES = 35
MAX_PHOTO_TOTAL_BYTES = 80 * 1024 * 1024

PLATFORM_LABELS = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "x": "X / Twitter",
}


class _YTDLPLogger:
    """Đưa log nội bộ của yt-dlp vào DEBUG thay vì in ERROR thẳng ra console."""

    def debug(self, message: str) -> None:
        logger.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        logger.debug("yt-dlp warning: %s", message)

    def error(self, message: str) -> None:
        logger.debug("yt-dlp error: %s", message)


class MediaDownloadError(RuntimeError):
    """Lỗi tải media có thể hiển thị an toàn cho người dùng."""


@dataclass(frozen=True)
class MediaItem:
    platform: str
    url: str
    title: str
    uploader: str
    duration: int
    thumbnail: str | None
    format_id: str | None = None
    direct_url: str | None = None
    direct_headers: dict[str, str] | None = None
    media_kind: str = "video"
    image_sources: tuple[tuple[str, ...], ...] = ()


def _clean_url(raw_url: str) -> str:
    return raw_url.strip().strip("<>").rstrip(".,!?;:'\"}>])")


def _platform_from_url(url: str) -> str | None:
    try:
        host = (urlparse(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return None

    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        return "youtube"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    if host in {"x.com", "twitter.com"} or host.endswith((".x.com", ".twitter.com")):
        return "x"
    return None


def _tiktok_photo_id(url: str) -> str | None:
    if _platform_from_url(url) != "tiktok":
        return None
    match = re.search(r"/photo/(\d+)(?:[/?#]|$)", urlparse(url).path + "/")
    return match.group(1) if match else None


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "Không rõ"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _safe_filename(title: str, extension: str) -> str:
    normalized = unicodedata.normalize("NFKC", title or "media")
    normalized = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ._")
    if not normalized:
        normalized = "media"
    return f"{normalized[:100]}.{extension}"


def _common_ydl_options(platform: str | None = None) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _YTDLPLogger(),
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 2,
        "extractor_args": {
            "youtube": ["player_client=ios,android,web", "player_skip=webpage"],
        },
    }
    # Không dùng chung cookie jar với TikTok/X. TikTok có thể ghi challenge cookie
    # tạm vào file, làm các lần trích xuất kế tiếp kẹt ở bước rehydration.
    if platform == "youtube" and os.path.isfile("cookies.txt"):
        options["cookiefile"] = "cookies.txt"
    return options


def _unwrap_single_video(info: dict) -> dict:
    entries = info.get("entries")
    if entries is not None:
        entries = [entry for entry in entries if entry]
        if len(entries) != 1:
            raise MediaDownloadError("Playlist chưa được hỗ trợ; hãy gửi link của một video cụ thể.")
        info = entries[0]
    if info.get("_type") in {"playlist", "multi_video"}:
        raise MediaDownloadError("Playlist hoặc nội dung nhiều phần chưa được hỗ trợ.")
    return info


def _format_size(media_format: dict) -> int:
    try:
        return int(media_format.get("filesize") or media_format.get("filesize_approx") or 0)
    except (TypeError, ValueError):
        return 0


def _estimated_format_size(media_format: dict, duration: int) -> int:
    size = _format_size(media_format)
    if size:
        return size
    try:
        bitrate = float(
            media_format.get("tbr")
            or media_format.get("vbr")
            or media_format.get("abr")
            or 0
        )
    except (TypeError, ValueError):
        bitrate = 0
    return int(bitrate * 1000 * duration / 8) if bitrate > 0 and duration > 0 else 0


def _select_mp4_format(info: dict, platform: str, upload_limit: int) -> str | None:
    safe_limit = max(1, upload_limit - UPLOAD_SAFETY_MARGIN)
    duration = int(info.get("duration") or 0)
    combined: list[dict] = []
    video_only: list[dict] = []
    audio_only: list[dict] = []

    for media_format in info.get("formats") or []:
        if not media_format.get("url"):
            continue

        note = str(media_format.get("format_note") or "").casefold()
        format_id = str(media_format.get("format_id") or "")
        if "unplayable" in note:
            continue
        if platform == "tiktok" and (
            "watermarked" in note or format_id.casefold() == "download"
        ):
            continue

        size = _estimated_format_size(media_format, duration)
        if size and size > safe_limit:
            continue

        vcodec = media_format.get("vcodec")
        acodec = media_format.get("acodec")
        height = int(media_format.get("height") or 0)
        looks_audio = (
            vcodec == "none"
            and (
                acodec not in {None, "none"}
                or "audio" in format_id.casefold()
                or str(media_format.get("resolution") or "").casefold() == "audio only"
            )
        )
        looks_video_only = vcodec not in {None, "none"} and acodec == "none"
        looks_combined = (
            vcodec not in {None, "none"} and acodec not in {None, "none"}
        ) or (platform == "x" and height > 0 and vcodec is None and acodec is None)

        if looks_combined:
            combined.append(media_format)
        elif platform == "x" and looks_video_only:
            video_only.append(media_format)
        elif platform == "x" and looks_audio:
            audio_only.append(media_format)

    def rank(media_format: dict) -> tuple:
        size = _estimated_format_size(media_format, duration)
        height = int(media_format.get("height") or 0)
        tbr = float(media_format.get("tbr") or 0)
        vcodec = str(media_format.get("vcodec") or "").casefold()
        extension = str(media_format.get("ext") or "").casefold()
        format_id = str(media_format.get("format_id") or "").casefold()
        h264 = vcodec.startswith(("h264", "avc"))
        direct_tiktok = platform != "tiktok" or format_id.startswith(("play", "h264"))
        sensible_height = height <= 720 or height == 0
        return (
            bool(size),
            direct_tiktok,
            extension == "mp4",
            h264,
            sensible_height,
            min(height, 720),
            tbr,
        )

    if combined:
        return str(max(combined, key=rank).get("format_id") or "") or None

    if platform != "x" or not video_only or not audio_only:
        return None

    pairs: list[tuple[dict, dict]] = []
    for video in video_only:
        for audio in audio_only:
            video_size = _estimated_format_size(video, duration)
            audio_size = _estimated_format_size(audio, duration)
            if video_size and audio_size and video_size + audio_size > safe_limit:
                continue
            pairs.append((video, audio))

    if not pairs:
        return None

    def pair_rank(pair: tuple[dict, dict]) -> tuple:
        video, audio = pair
        video_rank = rank(video)
        audio_bitrate = float(audio.get("abr") or audio.get("tbr") or 0)
        known_total = bool(
            _estimated_format_size(video, duration)
            and _estimated_format_size(audio, duration)
        )
        return known_total, *video_rank[2:], audio_bitrate

    video, audio = max(pairs, key=pair_rank)
    video_id = str(video.get("format_id") or "")
    audio_id = str(audio.get("format_id") or "")
    return f"{video_id}+{audio_id}" if video_id and audio_id else None


def _probe_media_sync(platform: str, url: str) -> MediaItem:
    options = _common_ydl_options(platform)
    options.update({"skip_download": True})

    ydl = None
    try:
        attempts = 3 if platform == "tiktok" else 1
        last_error: yt_dlp.utils.DownloadError | None = None
        for attempt in range(attempts):
            try:
                with yt_dlp.YoutubeDL(options) as current_ydl:
                    info = _unwrap_single_video(
                        current_ydl.extract_info(url, download=False)
                    )
                ydl = current_ydl
                break
            except yt_dlp.utils.DownloadError as error:
                last_error = error
                retryable = "universal data for rehydration" in str(error).casefold()
                if retryable and attempt + 1 < attempts:
                    logger.debug(
                        "TikTok rehydration thất bại, thử phiên sạch %s/%s",
                        attempt + 2,
                        attempts,
                    )
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise
        else:
            if last_error:
                raise last_error
            raise MediaDownloadError("Không thể khởi tạo phiên đọc TikTok.")
    except MediaDownloadError:
        raise
    except yt_dlp.utils.DownloadError as error:
        error_text = str(error).casefold()
        if platform == "tiktok" and "universal data for rehydration" in error_text:
            raise MediaDownloadError(
                "TikTok vừa thay đổi dữ liệu trang nên extractor chưa đọc được video này. "
                "Hãy cập nhật yt-dlp hoặc thử lại sau."
            ) from error
        if platform == "tiktok" and "ip address is blocked" in error_text:
            raise MediaDownloadError(
                "TikTok đang chặn IP hiện tại truy cập video này."
            ) from error
        raise MediaDownloadError(
            "Không đọc được nội dung này. Video có thể riêng tư, đã bị gỡ hoặc cần đăng nhập."
        ) from error

    live_status = str(info.get("live_status") or "").casefold()
    if info.get("is_live") or live_status in {"is_live", "is_upcoming", "post_live"}:
        raise MediaDownloadError("Livestream chưa được hỗ trợ tải xuống.")

    try:
        duration = int(float(info.get("duration") or 0))
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        raise MediaDownloadError("Không xác định được thời lượng của nội dung này.")
    if duration > MAX_MEDIA_DURATION:
        raise MediaDownloadError("Chỉ hỗ trợ video có thời lượng tối đa 10 phút.")

    format_id = None
    direct_url = None
    direct_headers = None
    if platform in {"tiktok", "x"}:
        format_id = _select_mp4_format(info, platform, DEFAULT_UPLOAD_LIMIT)
        if not format_id:
            if platform == "tiktok":
                raise MediaDownloadError("Không tìm thấy bản MP4 không watermark phù hợp để tải.")
            raise MediaDownloadError("Không tìm thấy bản MP4 có cả hình và tiếng phù hợp.")

        # URL playback của TikTok thường là CDN đã ký và không watermark. Giữ URL này
        # để nút tải không phải gọi trang TikTok lần thứ hai (rất dễ dính challenge).
        if platform == "tiktok":
            selected_format = next(
                (
                    media_format
                    for media_format in info.get("formats") or []
                    if str(media_format.get("format_id") or "") == format_id
                    and media_format.get("url")
                    and "watermarked"
                    not in str(media_format.get("format_note") or "").casefold()
                ),
                None,
            )
            if selected_format:
                direct_url = str(selected_format["url"])
                raw_headers = selected_format.get("http_headers") or info.get("http_headers")
                if isinstance(raw_headers, dict):
                    direct_headers = {
                        str(key): str(value)
                        for key, value in raw_headers.items()
                        if value is not None
                    }
                else:
                    direct_headers = {}
                cookie_header = ydl.cookiejar.get_cookie_header(direct_url) if ydl else None
                if cookie_header:
                    direct_headers["Cookie"] = cookie_header

    return MediaItem(
        platform=platform,
        url=str(info.get("webpage_url") or url),
        title=str(info.get("title") or "Nội dung không có tiêu đề")[:250],
        uploader=str(info.get("uploader") or info.get("channel") or "Không rõ")[:100],
        duration=duration,
        thumbnail=info.get("thumbnail"),
        format_id=format_id,
        direct_url=direct_url,
        direct_headers=direct_headers,
    )


def _probe_tiktok_photo_sync(url: str) -> MediaItem:
    photo_id = _tiktok_photo_id(url)
    if not photo_id:
        raise MediaDownloadError("Đây không phải link TikTok photo hợp lệ.")

    extractor_url = url.replace("/photo/", "/video/", 1)
    video_data = None
    ydl = None
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(_common_ydl_options("tiktok")) as current_ydl:
                extractor = current_ydl.get_info_extractor("TikTok")
                video_data, status = extractor._extract_web_data_and_status(
                    extractor_url,
                    photo_id,
                )
            ydl = current_ydl
            if video_data and status == 0:
                break
        except Exception as error:
            last_error = error
            if "universal data for rehydration" not in str(error).casefold():
                break
        time.sleep(0.25 * (attempt + 1))

    if not video_data:
        raise MediaDownloadError(
            "TikTok chưa trả dữ liệu album ảnh này; hãy thử lại sau."
        ) from last_error

    image_post = video_data.get("imagePost") or {}
    raw_images = image_post.get("images") or []
    image_sources: list[tuple[str, ...]] = []
    for image in raw_images[:MAX_TIKTOK_IMAGES]:
        url_list = ((image or {}).get("imageURL") or {}).get("urlList") or []
        candidates = tuple(
            dict.fromkeys(
                str(candidate)
                for candidate in url_list
                if str(candidate).startswith("https://")
            )
        )
        if candidates:
            image_sources.append(candidates)

    if not image_sources:
        raise MediaDownloadError("Không tìm thấy ảnh tải được trong TikTok post này.")

    author = video_data.get("author") or {}
    uploader = str(
        author.get("nickname")
        or author.get("uniqueId")
        or author.get("unique_id")
        or "TikTok"
    )[:100]
    title = str(video_data.get("desc") or "Album ảnh TikTok")[:250]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        ),
        "Referer": url,
    }
    cookie_header = ydl.cookiejar.get_cookie_header(image_sources[0][0]) if ydl else None
    if cookie_header:
        headers["Cookie"] = cookie_header

    return MediaItem(
        platform="tiktok",
        url=url,
        title=title,
        uploader=uploader,
        duration=0,
        thumbnail=image_sources[0][0],
        direct_headers=headers,
        media_kind="photo",
        image_sources=tuple(image_sources),
    )


def _choose_mp3_bitrate(duration: int, upload_limit: int) -> int | None:
    safe_bytes = max(1, upload_limit - UPLOAD_SAFETY_MARGIN)
    budget_kbps = safe_bytes * 8 / max(1, duration) / 1000
    for bitrate in (192, 160, 128, 112, 96):
        if bitrate <= budget_kbps:
            return bitrate
    return None


def _find_downloaded_file(directory: str, extension: str) -> str:
    candidates = [
        path
        for path in Path(directory).iterdir()
        if path.is_file()
        and path.suffix.casefold() == f".{extension}"
        and not path.name.endswith((".part", ".ytdl"))
    ]
    if not candidates:
        raise MediaDownloadError(f"Không tạo được file {extension.upper()} hợp lệ.")
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


def _download_direct_mp4_sync(item: MediaItem, upload_limit: int, directory: str) -> tuple[str, str]:
    if not item.direct_url:
        raise MediaDownloadError("Link tải TikTok tạm thời không còn hợp lệ.")

    destination = os.path.join(directory, "tiktok.mp4")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
        **(item.direct_headers or {}),
    }
    request = Request(item.direct_url, headers=headers)

    try:
        with urlopen(request, timeout=30) as response:
            try:
                content_length = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                content_length = 0
            if content_length and content_length > upload_limit:
                raise MediaDownloadError(
                    "Bản MP4 không watermark vượt giới hạn upload hiện tại của Discord."
                )

            downloaded = 0
            with open(destination, "wb") as output:
                while chunk := response.read(256 * 1024):
                    downloaded += len(chunk)
                    if downloaded > upload_limit:
                        raise MediaDownloadError(
                            "Bản MP4 không watermark vượt giới hạn upload hiện tại của Discord."
                        )
                    output.write(chunk)
    except MediaDownloadError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise MediaDownloadError(
            "Link TikTok tạm đã hết hạn hoặc CDN từ chối kết nối; hãy gửi lại link để tạo panel mới."
        ) from error

    if not os.path.isfile(destination) or os.path.getsize(destination) <= 0:
        raise MediaDownloadError("TikTok trả về một file MP4 rỗng.")
    return destination, _safe_filename(item.title, "mp4")


def _download_tiktok_images_sync(
    item: MediaItem,
    upload_limit: int,
    directory: str,
) -> list[tuple[str, str]]:
    if not item.image_sources:
        raise MediaDownloadError("Album TikTok này không có danh sách ảnh hợp lệ.")

    extension_by_type = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/avif": "avif",
    }
    downloaded_files: list[tuple[str, str]] = []
    total_bytes = 0

    for index, sources in enumerate(item.image_sources, start=1):
        last_error: Exception | None = None
        completed = False
        for source_url in sources:
            request = Request(source_url, headers=item.direct_headers or {})
            try:
                with urlopen(request, timeout=30) as response:
                    content_type = response.headers.get_content_type().casefold()
                    if not content_type.startswith("image/"):
                        raise MediaDownloadError("TikTok CDN không trả về dữ liệu ảnh hợp lệ.")
                    extension = extension_by_type.get(content_type, "jpg")
                    destination = os.path.join(directory, f"tiktok_{index:02d}.{extension}")

                    try:
                        content_length = int(response.headers.get("Content-Length") or 0)
                    except (TypeError, ValueError):
                        content_length = 0
                    if content_length and content_length > upload_limit:
                        raise MediaDownloadError(
                            f"Ảnh số {index} vượt giới hạn upload hiện tại của Discord."
                        )

                    image_bytes = 0
                    with open(destination, "wb") as output:
                        while chunk := response.read(256 * 1024):
                            image_bytes += len(chunk)
                            if image_bytes > upload_limit:
                                raise MediaDownloadError(
                                    f"Ảnh số {index} vượt giới hạn upload hiện tại của Discord."
                                )
                            if total_bytes + image_bytes > MAX_PHOTO_TOTAL_BYTES:
                                raise MediaDownloadError(
                                    "Toàn bộ album vượt giới hạn xử lý tạm 80 MiB."
                                )
                            output.write(chunk)

                if image_bytes <= 0:
                    raise MediaDownloadError(f"Ảnh số {index} bị rỗng.")
                total_bytes += image_bytes
                downloaded_files.append((destination, f"tiktok_{index:02d}.{extension}"))
                completed = True
                break
            except MediaDownloadError:
                raise
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                last_error = error

        if not completed:
            raise MediaDownloadError(
                f"TikTok CDN từ chối tải ảnh số {index}; hãy chạy lại `/download`."
            ) from last_error

    return downloaded_files


def _download_media_sync(item: MediaItem, upload_limit: int, directory: str) -> tuple[str, str]:
    if item.platform == "tiktok" and item.direct_url:
        return _download_direct_mp4_sync(item, upload_limit, directory)

    options = _common_ydl_options(item.platform)
    options.update({
        "outtmpl": os.path.join(directory, "%(id)s.%(ext)s"),
        "restrictfilenames": True,
        "overwrites": True,
    })

    extension: str
    if item.platform == "youtube":
        bitrate = _choose_mp3_bitrate(item.duration, upload_limit)
        if bitrate is None:
            raise MediaDownloadError(
                "Video quá dài để tạo MP3 chất lượng tối thiểu 96 kbps trong giới hạn upload hiện tại."
            )
        extension = "mp3"
        options.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(bitrate),
            }],
        })
    else:
        extension = "mp4"
        if not item.format_id:
            raise MediaDownloadError("Không còn tìm thấy định dạng MP4 phù hợp.")
        options["format"] = item.format_id
        options["merge_output_format"] = "mp4"
        options["max_filesize"] = max(1, upload_limit - UPLOAD_SAFETY_MARGIN)

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            _unwrap_single_video(ydl.extract_info(item.url, download=True))
    except MediaDownloadError:
        raise
    except yt_dlp.utils.DownloadError as error:
        raise MediaDownloadError(
            "Nguồn tải từ nền tảng đang từ chối hoặc file vượt giới hạn dung lượng."
        ) from error

    filepath = _find_downloaded_file(directory, extension)
    size = os.path.getsize(filepath)
    if size <= 0:
        raise MediaDownloadError("File tải về bị rỗng.")
    if size > upload_limit:
        size_mib = size / (1024 * 1024)
        limit_mib = upload_limit / (1024 * 1024)
        raise MediaDownloadError(
            f"File có dung lượng {size_mib:.1f} MiB, vượt giới hạn Discord {limit_mib:.1f} MiB."
        )

    return filepath, _safe_filename(item.title, extension)


def _build_media_embed(item: MediaItem) -> discord.Embed:
    if item.media_kind == "photo":
        output = f"{len(item.image_sources)} ảnh gốc"
        detail_name = "🖼️ Nội dung"
        detail_value = "TikTok photo post"
    else:
        output = "MP3" if item.platform == "youtube" else "MP4"
        detail_name = "⏱️ Thời lượng"
        detail_value = _format_duration(item.duration)
    if item.platform == "tiktok" and item.media_kind != "photo":
        output += " không watermark"

    embed = discord.Embed(
        title=item.title,
        color={"youtube": 0xFF0033, "tiktok": 0x25F4EE, "x": 0x1D9BF0}[item.platform],
    )
    embed.add_field(name="🌐 Nền tảng", value=PLATFORM_LABELS[item.platform], inline=True)
    embed.add_field(name=detail_name, value=detail_value, inline=True)
    embed.add_field(name="📦 Tải xuống", value=output, inline=True)
    embed.add_field(name="👤 Tác giả", value=item.uploader, inline=False)
    if item.thumbnail:
        embed.set_image(url=item.thumbnail)
    embed.set_footer(text="Bấm nút bên dưới để nhận file riêng tư • Hết hạn sau 10 phút")
    return embed


class MediaDownloadButton(discord.ui.Button):
    def __init__(self, item: MediaItem):
        if item.media_kind == "photo":
            label = "Tải ảnh"
            emoji = "🖼️"
        elif item.platform == "youtube":
            label = "Tải MP3"
            emoji = "⬇️"
        elif item.platform == "tiktok":
            label = "Tải MP4 không watermark"
            emoji = "⬇️"
        else:
            label = "Tải MP4"
            emoji = "⬇️"
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, MediaDownloadView):
            await view.download(interaction)


class MediaDownloadView(discord.ui.View):
    def __init__(self, cog: "MediaDownloader", item: MediaItem):
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.item = item
        self.message: discord.Message | None = None
        self.add_item(MediaDownloadButton(item))

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    async def download(self, interaction: discord.Interaction) -> None:
        user_id = interaction.user.id
        if user_id in self.cog.active_users:
            return await interaction.response.send_message(
                "⏳ Bạn đang có một file khác được xử lý. Hãy chờ file đó xong nhé.",
                ephemeral=True,
            )

        self.cog.active_users.add(user_id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        temp_dir: str | None = None
        try:
            async with self.cog.download_semaphore:
                upload_limit = int(
                    getattr(interaction, "filesize_limit", None) or DEFAULT_UPLOAD_LIMIT
                )
                item = self.item
                if item.media_kind == "photo" and not item.image_sources:
                    item = await asyncio.to_thread(_probe_tiktok_photo_sync, item.url)
                    self.item = item
                elif (
                    item.media_kind != "photo"
                    and item.platform == "tiktok"
                    and not item.direct_url
                ):
                    item = await asyncio.to_thread(
                        _probe_media_sync,
                        item.platform,
                        item.url,
                    )
                    self.item = item
                temp_dir = tempfile.mkdtemp(prefix="peto-media-")

                if item.media_kind == "photo":
                    downloaded_files = await asyncio.to_thread(
                        _download_tiktok_images_sync,
                        item,
                        upload_limit,
                        temp_dir,
                    )
                    batches: list[list[tuple[str, str]]] = []
                    current_batch: list[tuple[str, str]] = []
                    current_size = 0
                    for downloaded in downloaded_files:
                        file_size = os.path.getsize(downloaded[0])
                        if current_batch and (
                            len(current_batch) >= 10
                            or current_size + file_size > upload_limit
                        ):
                            batches.append(current_batch)
                            current_batch = []
                            current_size = 0
                        current_batch.append(downloaded)
                        current_size += file_size
                    if current_batch:
                        batches.append(current_batch)

                    for batch_index, batch in enumerate(batches, start=1):
                        uploads = [
                            discord.File(path, filename=filename)
                            for path, filename in batch
                        ]
                        try:
                            await interaction.followup.send(
                                (
                                    f"✅ Ảnh TikTok của bạn đã sẵn sàng "
                                    f"({batch_index}/{len(batches)}):"
                                ),
                                files=uploads,
                                ephemeral=True,
                            )
                        finally:
                            for upload in uploads:
                                upload.close()
                    return

                filepath, filename = await asyncio.to_thread(
                    _download_media_sync,
                    item,
                    upload_limit,
                    temp_dir,
                )

                upload = discord.File(filepath, filename=filename)
                try:
                    await interaction.followup.send(
                        "✅ File của bạn đã sẵn sàng:",
                        file=upload,
                        ephemeral=True,
                    )
                finally:
                    upload.close()
        except MediaDownloadError as error:
            logger.info(
                "Nút tải media bị từ chối (platform=%s, kind=%s, user=%s): %s",
                self.item.platform,
                self.item.media_kind,
                user_id,
                error,
            )
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
        except Exception:
            logger.exception(
                "Universal Media Downloader thất bại (platform=%s, user=%s)",
                self.item.platform,
                user_id,
            )
            await interaction.followup.send(
                "❌ Không thể chuẩn bị file lúc này. Hãy thử lại sau nhé.",
                ephemeral=True,
            )
        finally:
            self.cog.active_users.discard(user_id)
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)


class MediaDownloader(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.download_semaphore = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
        self.probe_semaphore = asyncio.Semaphore(MAX_PARALLEL_PROBES)
        self.active_users: set[int] = set()

    @app_commands.command(
        name="download",
        description="Tải media từ YouTube, TikTok hoặc X",
    )
    @app_commands.describe(link="Link YouTube, TikTok video/photo hoặc X/Twitter")
    async def download_command(
        self,
        interaction: discord.Interaction,
        link: str,
    ) -> None:
        url = _clean_url(link)
        platform = _platform_from_url(url)
        if not platform:
            return await interaction.response.send_message(
                "❌ Chỉ hỗ trợ link YouTube, TikTok và X/Twitter.",
                ephemeral=True,
            )

        await interaction.response.defer(thinking=True, ephemeral=True)
        is_tiktok_photo = bool(_tiktok_photo_id(url))
        try:
            async with self.probe_semaphore:
                if is_tiktok_photo:
                    item = await asyncio.to_thread(_probe_tiktok_photo_sync, url)
                else:
                    item = await asyncio.to_thread(_probe_media_sync, platform, url)
        except MediaDownloadError as error:
            if is_tiktok_photo and "chưa trả dữ liệu album" in str(error):
                logger.info(
                    "TikTok chưa trả photo metadata; tạo panel /download dự phòng: %s",
                    url,
                )
                item = MediaItem(
                    platform="tiktok",
                    url=url,
                    title="Album ảnh TikTok",
                    uploader="TikTok",
                    duration=0,
                    thumbnail=None,
                    media_kind="photo",
                )
            elif platform == "tiktok" and "extractor chưa đọc được" in str(error):
                # Vẫn dựng panel; nút tải sẽ thử TikTok lại khi người dùng bấm.
                logger.info(
                    "TikTok chưa trả metadata; tạo panel /download dự phòng: %s",
                    url,
                )
                item = MediaItem(
                    platform="tiktok",
                    url=url,
                    title="TikTok video",
                    uploader="TikTok",
                    duration=0,
                    thumbnail=None,
                )
            else:
                logger.info("Không thể tạo panel /download cho %s: %s", url, error)
                await interaction.edit_original_response(
                    content=f"❌ {error}",
                    embed=None,
                    view=None,
                )
                return
        except Exception:
            logger.exception("Không thể đọc media link cho /download: %s", url)
            await interaction.edit_original_response(
                content="❌ Không thể đọc link này lúc này. Hãy thử lại sau nhé.",
                embed=None,
                view=None,
            )
            return

        view = MediaDownloadView(self, item)
        view.message = await interaction.edit_original_response(
            content=None,
            embed=_build_media_embed(item),
            view=view,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MediaDownloader(bot))
