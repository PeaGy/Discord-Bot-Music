from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger(__name__)

PANEL_TIMEOUT = 10 * 60
# /download nhận nội dung ngắn hơn một giờ. Mốc 60:00 bị từ chối để giữ
# thời gian xử lý, dung lượng tạm và băng thông máy nhà trong phạm vi hợp lý.
MAX_MEDIA_DURATION = 60 * 60
DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024
UPLOAD_SAFETY_MARGIN = 128 * 1024
MAX_PARALLEL_DOWNLOADS = 2
MAX_PARALLEL_PROBES = 3
MAX_TIKTOK_IMAGES = 35
MAX_X_IMAGES = 4
MAX_PHOTO_TOTAL_BYTES = 80 * 1024 * 1024
FXTWITTER_RESPONSE_LIMIT = 4 * 1024 * 1024
MAX_GATEWAY_FILE_BYTES = max(
    10,
    int(os.getenv("DOWNLOAD_MAX_FILE_MIB", "512")),
) * 1024 * 1024
TIKWM_API_URL = "https://www.tikwm.com/api/"
TIKWM_RESPONSE_LIMIT = 2 * 1024 * 1024
_TIKTOK_MEDIA_HOST_SUFFIXES = (
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "tiktokcdn-eu.com",
    "byteoversea.com",
    "ibytedtos.com",
    "tikwm.com",
    "tikwmcdn.com",
)

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


class MediaDownloadProgress:
    """Trạng thái thread-safe; hook yt-dlp tuyệt đối không gọi Discord trực tiếp."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stage = "waiting"
        self.downloaded = 0
        self.total = 0
        self.speed = 0.0
        self.eta: int | None = None
        self.detail = ""
        self.revision = 0

    def set_stage(self, stage: str, detail: str = "", *, reset: bool = True) -> None:
        with self._lock:
            self.stage = stage
            self.detail = detail
            self.revision += 1
            if reset:
                self.downloaded = 0
                self.total = 0
                self.speed = 0.0
                self.eta = None

    def update_download(self, data: dict) -> None:
        status = data.get("status")
        if status == "finished":
            info = data.get("info_dict") if isinstance(data.get("info_dict"), dict) else {}
            vcodec = info.get("vcodec")
            acodec = info.get("acodec")
            if acodec == "none" and vcodec not in {None, "none"}:
                self.set_stage("audio_pending")
            elif vcodec == "none" and acodec not in {None, "none"}:
                self.set_stage("merging")
            else:
                self.set_stage("processing")
            return
        if status != "downloading":
            return
        info = data.get("info_dict") if isinstance(data.get("info_dict"), dict) else {}
        vcodec = info.get("vcodec")
        acodec = info.get("acodec")
        if vcodec == "none" and acodec not in {None, "none"}:
            stage = "audio"
        elif acodec == "none" and vcodec not in {None, "none"}:
            stage = "video"
        else:
            stage = "media"
        try:
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            speed = float(data.get("speed") or 0)
            eta_value = data.get("eta")
            eta = max(0, int(float(eta_value))) if eta_value is not None else None
        except (TypeError, ValueError):
            return
        with self._lock:
            # MP4 tách video/audio có thể chạy 100% rồi về 0%. Đổi tên giai đoạn
            # giúp người dùng hiểu đây là luồng thứ hai chứ không phải tải lại.
            self.stage = stage
            self.downloaded = downloaded
            self.total = total
            self.speed = speed
            self.eta = eta
            self.revision += 1

    def update_postprocessor(self, data: dict) -> None:
        status = data.get("status")
        name = str(data.get("postprocessor") or "FFmpeg")
        if status in {"started", "processing"}:
            stage = "merging" if name.casefold() == "merger" else "processing"
            self.set_stage(stage, name)
        elif status == "finished":
            if name.casefold() == "merger":
                self.set_stage("finalizing", "Kiểm tra container MP4")
            elif name.casefold() == "movefiles":
                self.set_stage("verifying")

    def set_counts(self, downloaded: int, total: int) -> None:
        with self._lock:
            self.downloaded = max(0, int(downloaded))
            self.total = max(0, int(total))
            self.revision += 1

    def snapshot(self) -> tuple[str, int, int, float, int | None, str, int]:
        with self._lock:
            return (
                self.stage,
                self.downloaded,
                self.total,
                self.speed,
                self.eta,
                self.detail,
                self.revision,
            )


@dataclass(frozen=True)
class YouTubeVideoVariant:
    height: int
    format_id: str
    estimated_size: int = 0


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
    output_format: str | None = None
    youtube_video_variants: tuple[YouTubeVideoVariant, ...] = ()
    youtube_audio_format_id: str | None = None


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


def _x_status_parts(url: str) -> tuple[str, str] | None:
    if _platform_from_url(url) != "x":
        return None
    match = re.search(
        r"^/([^/]+)/status/(\d+)(?:/(?:photo|video)/\d+)?/?$",
        urlparse(url).path,
        re.IGNORECASE,
    )
    return (match.group(1), match.group(2)) if match else None


def _trusted_x_media_url(raw_url: object) -> str | None:
    value = str(raw_url or "").strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not host:
        return None
    if host == "twimg.com" or host.endswith(".twimg.com"):
        return value
    return None


def _x_original_photo_url(url: str) -> str:
    trusted = _trusted_x_media_url(url)
    if not trusted:
        return url
    parsed = urlparse(trusted)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["name"] = "orig"
    return urlunparse(parsed._replace(query=urlencode(query)))


def _fxtwitter_status_sync(url: str) -> dict | None:
    parts = _x_status_parts(url)
    if not parts:
        return None
    username, status_id = parts
    endpoints = (
        f"https://api.fxtwitter.com/2/status/{status_id}",
        f"https://api.fxtwitter.com/{username}/status/{status_id}",
    )
    headers = {
        "Accept": "application/json",
        "User-Agent": "PetoDiscordBot/1.0 (personal media downloader)",
    }
    for endpoint in endpoints:
        try:
            with urlopen(Request(endpoint, headers=headers), timeout=20) as response:
                raw = response.read(FXTWITTER_RESPONSE_LIMIT + 1)
            if len(raw) > FXTWITTER_RESPONSE_LIMIT:
                logger.info("FxTwitter trả metadata quá lớn cho status=%s", status_id)
                continue
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                continue
            status = payload.get("status") or payload.get("tweet")
            if isinstance(status, dict):
                return status
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
            logger.debug("Không đọc được FxTwitter endpoint %s", endpoint, exc_info=True)
    return None


def _best_x_animation_url(media: dict) -> str | None:
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for media_format in media.get("formats") or []:
        if not isinstance(media_format, dict):
            continue
        url = _trusted_x_media_url(media_format.get("url"))
        container = str(media_format.get("container") or "").casefold()
        if not url or (container and container != "mp4"):
            continue
        try:
            size = int(media_format.get("size") or 0)
            height = int(media_format.get("height") or 0)
            bitrate = int(media_format.get("bitrate") or 0)
        except (TypeError, ValueError):
            size = height = bitrate = 0
        candidates.append(((height, bitrate, size), url))
    if candidates:
        return max(candidates, key=lambda entry: entry[0])[1]
    for key in ("transcode_url", "url"):
        trusted = _trusted_x_media_url(media.get(key))
        if trusted:
            return trusted
    return None


def _probe_x_special_media_sync(url: str) -> MediaItem | None:
    """Return X photos/GIFs via FxTwitter; normal videos stay on yt-dlp."""
    status = _fxtwitter_status_sync(url)
    if not status:
        return None
    media = status.get("media") if isinstance(status.get("media"), dict) else {}
    photos = [entry for entry in media.get("photos") or [] if isinstance(entry, dict)]
    videos = [entry for entry in media.get("videos") or [] if isinstance(entry, dict)]
    animation = next(
        (
            entry
            for entry in (*videos, *photos)
            if str(entry.get("type") or "").casefold() in {"gif", "animated_gif"}
        ),
        None,
    )
    author = status.get("author") if isinstance(status.get("author"), dict) else {}
    uploader = str(
        author.get("name") or author.get("screen_name") or "X / Twitter"
    )[:100]
    text = str(status.get("text") or "").strip()
    title = (text or f"Nội dung của {uploader}")[:250]
    headers = {
        "User-Agent": "Mozilla/5.0 (Peto Discord Bot media downloader)",
        "Referer": url,
    }

    if animation is not None:
        direct_url = _best_x_animation_url(animation)
        if not direct_url:
            return None
        try:
            duration = max(0, int(round(float(animation.get("duration") or 0))))
        except (TypeError, ValueError):
            duration = 0
        thumbnail = _trusted_x_media_url(animation.get("thumbnail_url"))
        return MediaItem(
            platform="x",
            url=str(status.get("url") or url),
            title=title,
            uploader=uploader,
            duration=duration,
            thumbnail=thumbnail,
            direct_url=direct_url,
            direct_headers=headers,
            media_kind="gif",
            output_format="gif",
        )

    image_sources: list[tuple[str, ...]] = []
    for photo in photos[:MAX_X_IMAGES]:
        if str(photo.get("type") or "photo").casefold() != "photo":
            continue
        source = _trusted_x_media_url(photo.get("url"))
        if not source:
            continue
        image_sources.append(tuple(dict.fromkeys((_x_original_photo_url(source), source))))
    if not image_sources:
        return None
    return MediaItem(
        platform="x",
        url=str(status.get("url") or url),
        title=title,
        uploader=uploader,
        duration=0,
        thumbnail=image_sources[0][0],
        direct_headers=headers,
        media_kind="photo",
        image_sources=tuple(image_sources),
    )


def _trusted_tiktok_media_url(raw_url: object) -> str | None:
    """Chỉ nhận HTTPS từ CDN TikTok/TikWM, không tin URL tùy ý từ API phụ."""
    value = str(raw_url or "").strip()
    if value.startswith("//"):
        value = f"https:{value}"
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not host:
        return None
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in _TIKTOK_MEDIA_HOST_SUFFIXES):
        return None
    return value


def _tikwm_headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
    }


def _probe_tikwm_sync(url: str) -> MediaItem:
    """Fallback TikWM cho TikTok video/photo khi yt-dlp bị challenge."""
    request = Request(
        TIKWM_API_URL,
        data=urlencode({"url": url, "hd": "1"}).encode("utf-8"),
        headers={
            **_tikwm_headers("https://www.tikwm.com/"),
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(TIKWM_RESPONSE_LIMIT + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise MediaDownloadError("TikWM fallback hiện không kết nối được.") from error

    if len(raw) > TIKWM_RESPONSE_LIMIT:
        raise MediaDownloadError("TikWM fallback trả metadata quá lớn.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise MediaDownloadError("TikWM fallback trả dữ liệu không hợp lệ.") from error

    if not isinstance(payload, dict) or payload.get("code") != 0:
        detail = str(payload.get("msg") or "không rõ nguyên nhân")[:160] if isinstance(payload, dict) else "không rõ nguyên nhân"
        raise MediaDownloadError(f"TikWM fallback từ chối link này: {detail}.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MediaDownloadError("TikWM fallback không trả metadata media.")

    author = data.get("author") if isinstance(data.get("author"), dict) else {}
    uploader = str(
        author.get("nickname")
        or author.get("unique_id")
        or author.get("uniqueId")
        or "TikTok"
    )[:100]
    title = str(data.get("title") or "Nội dung TikTok")[:250]
    cover = _trusted_tiktok_media_url(
        data.get("cover") or data.get("origin_cover")
    )
    headers = _tikwm_headers(url)

    raw_images = data.get("images")
    if isinstance(raw_images, list) and raw_images:
        image_sources: list[tuple[str, ...]] = []
        for raw_image in raw_images[:MAX_TIKTOK_IMAGES]:
            candidates = raw_image if isinstance(raw_image, list) else [raw_image]
            trusted = tuple(
                dict.fromkeys(
                    candidate
                    for candidate in (
                        _trusted_tiktok_media_url(value) for value in candidates
                    )
                    if candidate
                )
            )
            if trusted:
                image_sources.append(trusted)
        if not image_sources:
            raise MediaDownloadError("TikWM fallback không trả URL ảnh TikTok an toàn.")
        logger.info("TikWM fallback đọc được TikTok photo (%s ảnh): %s", len(image_sources), url)
        return MediaItem(
            platform="tiktok",
            url=url,
            title=title or "Album ảnh TikTok",
            uploader=uploader,
            duration=0,
            thumbnail=cover or image_sources[0][0],
            direct_headers=headers,
            media_kind="photo",
            image_sources=tuple(image_sources),
        )

    direct_url = _trusted_tiktok_media_url(data.get("hdplay")) or _trusted_tiktok_media_url(data.get("play"))
    if not direct_url:
        raise MediaDownloadError("TikWM fallback không trả MP4 không watermark an toàn.")
    try:
        duration = int(float(data.get("duration") or 0))
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        raise MediaDownloadError("TikWM fallback không xác định được thời lượng video.")
    if duration >= MAX_MEDIA_DURATION:
        raise MediaDownloadError("Chỉ hỗ trợ nội dung có thời lượng dưới 60 phút.")

    logger.info("TikWM fallback đọc được TikTok video: %s", url)
    return MediaItem(
        platform="tiktok",
        url=url,
        title=title,
        uploader=uploader,
        duration=duration,
        thumbnail=cover,
        direct_url=direct_url,
        direct_headers=headers,
    )


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "Không rõ"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _format_bytes(value: int | float) -> str:
    size = max(0.0, float(value or 0))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit in {"B", "KiB"} else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def _format_eta(seconds: int | None) -> str:
    if seconds is None:
        return "chưa rõ"
    if seconds < 60:
        return f"{seconds} giây"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes} phút {seconds:02d} giây"


def _render_download_progress(progress: MediaDownloadProgress) -> str:
    stage, downloaded, total, speed, eta, detail, _revision = progress.snapshot()
    labels = {
        "waiting": "⏳ **Đang chờ lượt tải…**",
        "preparing": "🔎 **Đang chuẩn bị nguồn tải…**",
        "video": "🎬 **Đang tải hình ảnh…**",
        "audio_pending": "🎵 **Hình ảnh đã xong — đang chuẩn bị audio gốc…**",
        "audio": "🎵 **Đang tải audio gốc…**",
        "media": "⬇️ **Đang tải media…**",
        "images": "🖼️ **Đang tải ảnh…**",
        "processing": "🔧 **Đang ghép/chuyển đổi file…**",
        "merging": "🔧 **Đang ghép hình ảnh và audio gốc…**",
        "finalizing": "📦 **Đang hoàn thiện file MP4…**",
        "verifying": "🔍 **Đang kiểm tra file kết quả…**",
        "uploading": "📤 **Đang gửi file lên Discord…**",
        "publishing": "☁️ **Đang tạo liên kết tải riêng…**",
    }
    lines = [labels.get(stage, "⏳ **Đang xử lý…**")]
    if stage in {"video", "audio", "media", "images"}:
        if total > 0:
            ratio = min(1.0, downloaded / total)
            filled = min(12, int(ratio * 12))
            bar = "█" * filled + "░" * (12 - filled)
            lines.append(f"`{bar}` **{ratio * 100:.0f}%**")
            if stage == "images":
                lines.append(f"`{downloaded}/{total} ảnh`")
            else:
                lines.append(f"`{_format_bytes(downloaded)} / {_format_bytes(total)}`")
        elif downloaded > 0:
            lines.append(f"Đã nhận `{_format_bytes(downloaded)}`")
        extras = []
        if speed > 0:
            extras.append(f"{_format_bytes(speed)}/s")
        if eta is not None:
            extras.append(f"còn khoảng {_format_eta(eta)}")
        if extras:
            lines.append(" • ".join(extras))
    elif detail:
        lines.append(f"`{detail[:100]}`")
    lines.append("-# Bạn có thể tiếp tục dùng Discord; Peto sẽ báo khi file sẵn sàng.")
    return "\n".join(lines)


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
        elif platform in {"x", "youtube"} and looks_video_only:
            video_only.append(media_format)
        elif platform in {"x", "youtube"} and looks_audio:
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

    if platform not in {"x", "youtube"} or not video_only or not audio_only:
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


def _select_youtube_mp4_variants(
    info: dict,
    output_limit: int,
) -> tuple[YouTubeVideoVariant, ...]:
    """Chọn tối đa ba bản MP4 phổ biến, có audio và không vượt giới hạn gateway."""
    safe_limit = max(1, output_limit - UPLOAD_SAFETY_MARGIN)
    duration = int(info.get("duration") or 0)
    formats = [
        media_format
        for media_format in info.get("formats") or []
        if media_format.get("url")
        and "unplayable" not in str(media_format.get("format_note") or "").casefold()
    ]
    audio_only = [
        media_format
        for media_format in formats
        if media_format.get("vcodec") == "none"
        and media_format.get("acodec") not in {None, "none"}
    ]

    original_language = str(info.get("language") or "").casefold()

    def audio_rank(media_format: dict) -> tuple:
        extension = str(media_format.get("ext") or "").casefold()
        acodec = str(media_format.get("acodec") or "").casefold()
        language = str(media_format.get("language") or "").casefold()
        note = str(media_format.get("format_note") or "").casefold()
        try:
            language_preference = float(media_format.get("language_preference") or 0)
        except (TypeError, ValueError):
            language_preference = 0
        bitrate = float(media_format.get("abr") or media_format.get("tbr") or 0)
        is_original = "original" in note or "default" in note or language_preference > 0
        matches_original_language = bool(
            original_language
            and language
            and (
                language == original_language
                or language.split("-", 1)[0] == original_language.split("-", 1)[0]
            )
        )
        return (
            is_original,
            language_preference,
            matches_original_language,
            extension in {"m4a", "mp4"},
            acodec.startswith("mp4a"),
            bitrate,
        )

    audio_only.sort(key=audio_rank, reverse=True)
    candidates_by_height: dict[int, list[tuple[tuple, YouTubeVideoVariant]]] = {}

    for video in formats:
        try:
            height = int(video.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        if height <= 0 or height > 1080 or video.get("vcodec") in {None, "none"}:
            continue

        video_id = str(video.get("format_id") or "")
        if not video_id:
            continue
        video_size = _estimated_format_size(video, duration)
        acodec = video.get("acodec")
        selected_audio: dict | None = None
        format_id = video_id
        estimated_size = video_size

        if acodec == "none":
            for audio in audio_only:
                audio_size = _estimated_format_size(audio, duration)
                if video_size and audio_size and video_size + audio_size > safe_limit:
                    continue
                selected_audio = audio
                audio_id = str(audio.get("format_id") or "")
                if audio_id:
                    format_id = f"{video_id}+{audio_id}"
                    estimated_size = video_size + audio_size if video_size and audio_size else 0
                    break
            if selected_audio is None:
                continue

        if estimated_size and estimated_size > safe_limit:
            continue

        extension = str(video.get("ext") or "").casefold()
        vcodec = str(video.get("vcodec") or "").casefold()
        dynamic_range = str(video.get("dynamic_range") or "SDR").upper()
        fps = float(video.get("fps") or 0)
        bitrate = float(video.get("tbr") or 0)
        mp4_compatible_audio = selected_audio is None or (
            str(selected_audio.get("ext") or "").casefold() in {"m4a", "mp4"}
            and str(selected_audio.get("acodec") or "").casefold().startswith("mp4a")
        )
        rank = (
            extension == "mp4",
            vcodec.startswith(("avc", "h264")),
            mp4_compatible_audio,
            dynamic_range == "SDR",
            bool(estimated_size),
            fps,
            bitrate,
        )
        candidates_by_height.setdefault(height, []).append(
            (rank, YouTubeVideoVariant(height, format_id, estimated_size))
        )

    best_by_height = {
        height: max(candidates, key=lambda candidate: candidate[0])[1]
        for height, candidates in candidates_by_height.items()
    }
    if not best_by_height:
        return ()

    # Ưu tiên đúng ba mốc quen thuộc. Nếu nguồn thiếu một mốc, lấy bản gần nhất
    # nhưng không tạo hai nút trỏ tới cùng một độ phân giải.
    selected: list[YouTubeVideoVariant] = []
    remaining_heights = set(best_by_height)
    for target in (360, 720, 1080):
        if not remaining_heights:
            break
        height = min(
            remaining_heights,
            key=lambda value: (abs(value - target), value > target, -value),
        )
        selected.append(best_by_height[height])
        remaining_heights.remove(height)

    return tuple(sorted(selected, key=lambda variant: variant.height))


def _select_youtube_original_audio(info: dict) -> str | None:
    """Chọn audio gốc/default tốt nhất, không để track lồng tiếng thắng vì bitrate."""
    original_language = str(info.get("language") or "").casefold()
    candidates: list[dict] = []
    for media_format in info.get("formats") or []:
        if (
            media_format.get("url")
            and media_format.get("vcodec") == "none"
            and media_format.get("acodec") not in {None, "none"}
            and "unplayable"
            not in str(media_format.get("format_note") or "").casefold()
        ):
            candidates.append(media_format)
    if not candidates:
        return None

    def rank(media_format: dict) -> tuple:
        language = str(media_format.get("language") or "").casefold()
        note = str(media_format.get("format_note") or "").casefold()
        try:
            language_preference = float(media_format.get("language_preference") or 0)
        except (TypeError, ValueError):
            language_preference = 0
        matches_original_language = bool(
            original_language
            and language
            and (
                language == original_language
                or language.split("-", 1)[0] == original_language.split("-", 1)[0]
            )
        )
        is_original = "original" in note or "default" in note or language_preference > 0
        bitrate = float(media_format.get("abr") or media_format.get("tbr") or 0)
        quality = float(media_format.get("quality") or 0)
        return is_original, language_preference, matches_original_language, quality, bitrate

    return str(max(candidates, key=rank).get("format_id") or "") or None


def _probe_media_sync(
    platform: str,
    url: str,
    output_limit: int = DEFAULT_UPLOAD_LIMIT,
    requested_format: str | None = None,
) -> MediaItem:
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
        if platform == "tiktok" and any(
            marker in error_text
            for marker in (
                "universal data for rehydration",
                "ip address is blocked",
                "sign in to confirm",
                "challenge",
            )
        ):
            logger.info("yt-dlp không đọc được TikTok; chuyển TikWM fallback: %s", url)
            try:
                return _probe_tikwm_sync(url)
            except MediaDownloadError as fallback_error:
                logger.info("Cả yt-dlp và TikWM đều không đọc được %s: %s", url, fallback_error)
                raise MediaDownloadError(
                    "Cả yt-dlp và TikWM fallback đều chưa đọc được TikTok này. "
                    "Hãy thử lại sau; nếu nhiều link cùng lỗi, cập nhật `yt-dlp` "
                    "trong `requirements.txt` rồi khởi động lại bot."
                ) from fallback_error
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
    if duration >= MAX_MEDIA_DURATION:
        raise MediaDownloadError("Chỉ hỗ trợ nội dung có thời lượng dưới 60 phút.")

    format_id = None
    direct_url = None
    direct_headers = None
    youtube_video_variants: tuple[YouTubeVideoVariant, ...] = ()
    youtube_audio_format_id = None
    if platform == "youtube":
        youtube_video_variants = _select_youtube_mp4_variants(info, output_limit)
        youtube_audio_format_id = _select_youtube_original_audio(info)
    wants_mp4 = platform in {"tiktok", "x"} or (
        platform == "youtube" and requested_format == "mp4"
    )
    if wants_mp4:
        if platform == "youtube" and youtube_video_variants:
            format_id = youtube_video_variants[-1].format_id
        else:
            format_id = _select_mp4_format(info, platform, output_limit)
        if not format_id:
            if platform == "tiktok":
                raise MediaDownloadError("Không tìm thấy bản MP4 không watermark phù hợp để tải.")
            raise MediaDownloadError(
                "Không tìm thấy bản MP4 có cả hình và tiếng phù hợp trong giới hạn tải."
            )

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
        output_format="mp4" if wants_mp4 else "mp3",
        youtube_video_variants=youtube_video_variants,
        youtube_audio_format_id=youtube_audio_format_id,
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
        logger.info("yt-dlp không đọc được TikTok photo; chuyển TikWM fallback: %s", url)
        try:
            fallback = _probe_tikwm_sync(url)
        except MediaDownloadError as fallback_error:
            raise MediaDownloadError(
                "Cả yt-dlp và TikWM fallback đều chưa đọc được album TikTok này. "
                "Hãy thử lại sau; nếu nhiều link cùng lỗi, cập nhật `yt-dlp` "
                "trong `requirements.txt` rồi khởi động lại bot."
            ) from fallback_error
        if fallback.media_kind != "photo":
            raise MediaDownloadError("TikWM không nhận diện link này là TikTok photo.")
        return fallback

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
    for bitrate in (320, 256, 224, 192, 160, 128, 112, 96):
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


def _download_direct_mp4_sync(
    item: MediaItem,
    upload_limit: int,
    directory: str,
    progress: MediaDownloadProgress | None = None,
) -> tuple[str, str]:
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
                    "Bản MP4 không watermark vượt giới hạn tải ngoài hiện tại."
                )

            downloaded = 0
            if progress:
                progress.set_stage("media")
            with open(destination, "wb") as output:
                while chunk := response.read(256 * 1024):
                    downloaded += len(chunk)
                    if downloaded > upload_limit:
                        raise MediaDownloadError(
                            "Bản MP4 không watermark vượt giới hạn tải ngoài hiện tại."
                        )
                    output.write(chunk)
                    if progress:
                        progress.update_download({
                            "status": "downloading",
                            "downloaded_bytes": downloaded,
                            "total_bytes": content_length,
                            "info_dict": {"vcodec": "unknown", "acodec": "unknown"},
                        })
    except MediaDownloadError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise MediaDownloadError(
            "Link TikTok tạm đã hết hạn hoặc CDN từ chối kết nối; hãy gửi lại link để tạo panel mới."
        ) from error

    if not os.path.isfile(destination) or os.path.getsize(destination) <= 0:
        raise MediaDownloadError("TikTok trả về một file MP4 rỗng.")
    return destination, _safe_filename(item.title, "mp4")


def _download_images_sync(
    item: MediaItem,
    upload_limit: int,
    directory: str,
    progress: MediaDownloadProgress | None = None,
) -> list[tuple[str, str]]:
    if not item.image_sources:
        raise MediaDownloadError("Bài đăng này không có danh sách ảnh hợp lệ.")

    extension_by_type = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/avif": "avif",
        "image/gif": "gif",
    }
    platform_label = PLATFORM_LABELS.get(item.platform, "nền tảng")
    file_prefix = "x" if item.platform == "x" else "tiktok"
    downloaded_files: list[tuple[str, str]] = []
    total_bytes = 0
    if progress:
        progress.set_stage("images", f"0/{len(item.image_sources)} ảnh")

    for index, sources in enumerate(item.image_sources, start=1):
        last_error: Exception | None = None
        completed = False
        for source_url in sources:
            request = Request(source_url, headers=item.direct_headers or {})
            try:
                with urlopen(request, timeout=30) as response:
                    content_type = response.headers.get_content_type().casefold()
                    if not content_type.startswith("image/"):
                        raise MediaDownloadError(
                            f"CDN {platform_label} không trả về dữ liệu ảnh hợp lệ."
                        )
                    extension = extension_by_type.get(content_type, "jpg")
                    destination = os.path.join(
                        directory,
                        f"{file_prefix}_{index:02d}.{extension}",
                    )

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
                downloaded_files.append(
                    (destination, f"{file_prefix}_{index:02d}.{extension}")
                )
                if progress:
                    progress.set_stage(
                        "images",
                        f"{index}/{len(item.image_sources)} ảnh",
                        reset=False,
                    )
                    progress.set_counts(index, len(item.image_sources))
                completed = True
                break
            except MediaDownloadError:
                raise
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                last_error = error

        if not completed:
            raise MediaDownloadError(
                f"CDN {platform_label} từ chối tải ảnh số {index}; "
                "hãy chạy lại `/download`."
            ) from last_error

    return downloaded_files


def _media_ffmpeg_executable() -> str:
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


def _run_media_ffmpeg(command: list[str]) -> None:
    try:
        result = subprocess.run(
            [_media_ffmpeg_executable(), *command],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError as error:
        raise MediaDownloadError("Không tìm thấy FFmpeg để chuyển ảnh động.") from error
    except subprocess.TimeoutExpired as error:
        raise MediaDownloadError("FFmpeg chuyển ảnh động quá thời gian cho phép.") from error
    if result.returncode != 0:
        logger.debug("FFmpeg ảnh động thất bại: %s", (result.stderr or "")[-1000:])
        raise MediaDownloadError("FFmpeg không chuyển được ảnh động này.")


def _convert_x_animation_sync(
    source_path: str,
    source_extension: str,
    output_format: str,
    output_limit: int,
    directory: str,
    progress: MediaDownloadProgress | None,
) -> str:
    if output_format == source_extension:
        return source_path
    if progress:
        progress.set_stage(
            "processing",
            "Đang chuyển MP4 thành GIF" if output_format == "gif" else "Đang tạo MP4 nhẹ hơn",
        )

    output_path = os.path.join(directory, f"x_animation.{output_format}")
    if output_format == "mp4":
        _run_media_ffmpeg([
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", source_path,
            "-an", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
            "-movflags", "+faststart", output_path,
        ])
        if os.path.isfile(output_path) and 0 < os.path.getsize(output_path) <= output_limit:
            return output_path
        raise MediaDownloadError("MP4 ảnh động vượt giới hạn tải ngoài hiện tại.")

    # X lưu GIF dưới dạng MP4. Dùng palette hai bước trong một filter graph để
    # tạo GIF thật; tự hạ fps/kích thước nếu file đầu tiên còn quá lớn.
    for fps, max_width in ((15, 960), (12, 720), (10, 480)):
        filter_graph = (
            f"fps={fps},scale='min({max_width},iw)':-2:flags=lanczos,"
            "split[s0][s1];[s0]palettegen=max_colors=256[p];"
            "[s1][p]paletteuse=dither=sierra2_4a"
        )
        _run_media_ffmpeg([
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", source_path,
            "-filter_complex", filter_graph,
            "-loop", "0", output_path,
        ])
        if os.path.isfile(output_path) and 0 < os.path.getsize(output_path) <= output_limit:
            return output_path
    raise MediaDownloadError(
        "GIF sau khi chuyển đổi vẫn vượt giới hạn tải ngoài; hãy chọn MP4 nhẹ hơn."
    )


def _download_x_animation_sync(
    item: MediaItem,
    output_limit: int,
    directory: str,
    progress: MediaDownloadProgress | None = None,
) -> tuple[str, str]:
    if not item.direct_url or not _trusted_x_media_url(item.direct_url):
        raise MediaDownloadError("Link ảnh động X không còn hợp lệ.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Peto Discord Bot media downloader)",
        "Referer": item.url,
        **(item.direct_headers or {}),
    }
    request = Request(item.direct_url, headers=headers)
    if progress:
        progress.set_stage("media", "Đang tải ảnh động từ X")
    try:
        with urlopen(request, timeout=60) as response:
            content_type = response.headers.get_content_type().casefold()
            source_extension = "gif" if content_type == "image/gif" else "mp4"
            source_path = os.path.join(directory, f"x_source.{source_extension}")
            try:
                content_length = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                content_length = 0
            if content_length and content_length > output_limit:
                raise MediaDownloadError("Ảnh động X vượt giới hạn tải ngoài hiện tại.")

            downloaded = 0
            with open(source_path, "wb") as output:
                while chunk := response.read(256 * 1024):
                    downloaded += len(chunk)
                    if downloaded > output_limit:
                        raise MediaDownloadError("Ảnh động X vượt giới hạn tải ngoài hiện tại.")
                    output.write(chunk)
                    if progress:
                        progress.update_download({
                            "status": "downloading",
                            "downloaded_bytes": downloaded,
                            "total_bytes": content_length,
                            "info_dict": {"vcodec": "unknown", "acodec": "none"},
                        })
    except MediaDownloadError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise MediaDownloadError(
            "CDN X từ chối tải ảnh động; hãy chạy lại `/download` để làm mới link."
        ) from error

    if not os.path.isfile(source_path) or os.path.getsize(source_path) <= 0:
        raise MediaDownloadError("X trả về file ảnh động rỗng.")
    output_format = "mp4" if item.output_format == "mp4" else "gif"
    output_path = _convert_x_animation_sync(
        source_path,
        source_extension,
        output_format,
        output_limit,
        directory,
        progress,
    )
    return output_path, _safe_filename(item.title, output_format)


def _download_media_sync(
    item: MediaItem,
    upload_limit: int,
    directory: str,
    progress: MediaDownloadProgress | None = None,
) -> tuple[str, str]:
    if item.platform == "tiktok" and item.direct_url:
        return _download_direct_mp4_sync(item, upload_limit, directory, progress)
    if item.platform == "x" and item.media_kind == "gif":
        return _download_x_animation_sync(item, upload_limit, directory, progress)

    options = _common_ydl_options(item.platform)
    options.update({
        "outtmpl": os.path.join(directory, "%(id)s.%(ext)s"),
        "restrictfilenames": True,
        "overwrites": True,
    })
    if progress:
        progress.set_stage("preparing")
        options["progress_hooks"] = [progress.update_download]
        options["postprocessor_hooks"] = [progress.update_postprocessor]

    extension: str
    if item.platform == "youtube" and item.output_format != "mp4":
        bitrate = _choose_mp3_bitrate(item.duration, upload_limit)
        if bitrate is None:
            raise MediaDownloadError(
                "Video quá dài để tạo MP3 chất lượng tối thiểu 96 kbps trong giới hạn upload hiện tại."
            )
        extension = "mp3"
        options.update({
            "format": item.youtube_audio_format_id or "bestaudio/best",
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
        options["remuxvideo"] = "mp4"
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

    if progress:
        progress.set_stage("verifying")

    filepath = _find_downloaded_file(directory, extension)
    size = os.path.getsize(filepath)
    if size <= 0:
        raise MediaDownloadError("File tải về bị rỗng.")
    if size > upload_limit:
        size_mib = size / (1024 * 1024)
        limit_mib = upload_limit / (1024 * 1024)
        raise MediaDownloadError(
            f"File có dung lượng {size_mib:.1f} MiB, vượt giới hạn xử lý {limit_mib:.1f} MiB."
        )

    return filepath, _safe_filename(item.title, extension)


def _build_media_embed(item: MediaItem) -> discord.Embed:
    if item.media_kind == "photo":
        output = f"{len(item.image_sources)} ảnh gốc"
        detail_name = "🖼️ Nội dung"
        detail_value = (
            "Bài đăng ảnh X / Twitter"
            if item.platform == "x"
            else "Bài đăng ảnh TikTok"
        )
    elif item.media_kind == "gif":
        output = "GIF thật • MP4 nhẹ hơn"
        detail_name = "🎞️ Nội dung"
        detail_value = "Ảnh động từ X / Twitter"
        if item.duration:
            detail_value += f" • {_format_duration(item.duration)}"
    else:
        if item.platform == "youtube" and item.youtube_video_variants:
            qualities = ", ".join(
                f"{variant.height}p" for variant in item.youtube_video_variants
            )
            output = f"MP3 chất lượng cao • MP4 {qualities}"
        else:
            output = "MP3" if item.output_format == "mp3" else "MP4"
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
    def __init__(
        self,
        item: MediaItem,
        *,
        label: str | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.success,
    ):
        if item.media_kind == "photo":
            default_label = "Tải ảnh"
            emoji = "🖼️"
        elif item.media_kind == "gif" and item.output_format == "gif":
            default_label = "Tải GIF"
            emoji = "🎞️"
        elif item.media_kind == "gif":
            default_label = "MP4 nhẹ hơn"
            emoji = "🎬"
        elif item.platform == "youtube" and item.output_format != "mp4":
            default_label = "MP3 chất lượng cao"
            emoji = "🎵"
        elif item.platform == "tiktok":
            default_label = "Tải MP4 không watermark"
            emoji = "⬇️"
        else:
            default_label = "Tải MP4"
            emoji = "🎬" if item.platform == "youtube" else "⬇️"
        super().__init__(label=label or default_label, emoji=emoji, style=style)
        self.download_item = item

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, MediaDownloadView):
            await view.download(interaction, self.download_item)


class MediaDownloadView(discord.ui.View):
    def __init__(self, cog: "MediaDownloader", item: MediaItem):
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.item = item
        self.message: discord.Message | None = None
        if item.platform == "youtube":
            self.add_item(
                MediaDownloadButton(
                    replace(item, output_format="mp3", format_id=None),
                )
            )
            for variant in item.youtube_video_variants:
                self.add_item(
                    MediaDownloadButton(
                        replace(
                            item,
                            output_format="mp4",
                            format_id=variant.format_id,
                        ),
                        label=f"MP4 {variant.height}p",
                        style=discord.ButtonStyle.primary,
                    )
                )
        elif item.platform == "x" and item.media_kind == "gif":
            self.add_item(MediaDownloadButton(replace(item, output_format="gif")))
            self.add_item(
                MediaDownloadButton(
                    replace(item, output_format="mp4"),
                    style=discord.ButtonStyle.primary,
                )
            )
        else:
            self.add_item(MediaDownloadButton(item))

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    @staticmethod
    async def _run_progress_tracker(
        interaction: discord.Interaction,
        progress: MediaDownloadProgress,
        stop_event: asyncio.Event,
    ) -> None:
        last_content = ""
        last_stage = ""
        last_revision = -1
        last_edit_at = 0.0
        while not stop_event.is_set():
            stage, *_values, revision = progress.snapshot()
            now = asyncio.get_running_loop().time()
            stage_changed = stage != last_stage
            progress_due = revision != last_revision and now - last_edit_at >= 4.0
            if stage_changed or progress_due or not last_content:
                content = _render_download_progress(progress)
                try:
                    await interaction.edit_original_response(content=content)
                    last_content = content
                    last_stage = stage
                    last_revision = revision
                    last_edit_at = now
                except (discord.NotFound, discord.Forbidden):
                    return
                except discord.HTTPException:
                    logger.debug("Không thể cập nhật Download Tracker lúc này", exc_info=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

    async def download(
        self,
        interaction: discord.Interaction,
        selected_item: MediaItem | None = None,
    ) -> None:
        user_id = interaction.user.id
        if user_id in self.cog.active_users:
            return await interaction.response.send_message(
                "⏳ Bạn đang có một file khác được xử lý. Hãy chờ file đó xong nhé.",
                ephemeral=True,
            )

        self.cog.active_users.add(user_id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        temp_dir: str | None = None
        progress = MediaDownloadProgress()
        stop_tracker = asyncio.Event()
        tracker_task = asyncio.create_task(
            self._run_progress_tracker(interaction, progress, stop_tracker),
            name=f"media-download-progress-{interaction.id}",
        )

        async def stop_progress_tracker() -> None:
            stop_tracker.set()
            try:
                await tracker_task
            except asyncio.CancelledError:
                pass

        try:
            async with self.cog.download_semaphore:
                upload_limit = int(
                    getattr(interaction, "filesize_limit", None) or DEFAULT_UPLOAD_LIMIT
                )
                gateway = self.cog.bot.get_cog("DownloadGateway")
                gateway_enabled = bool(gateway and getattr(gateway, "enabled", False))
                processing_limit = (
                    max(upload_limit, int(getattr(gateway, "max_file_bytes", 0)))
                    if gateway_enabled
                    else upload_limit
                )
                item = selected_item or self.item
                if (
                    item.platform == "tiktok"
                    and item.media_kind == "photo"
                    and not item.image_sources
                ):
                    progress.set_stage("preparing", "Đang đọc album TikTok")
                    item = await asyncio.to_thread(_probe_tiktok_photo_sync, item.url)
                    self.item = item
                elif (
                    item.media_kind != "photo"
                    and item.platform == "tiktok"
                    and not item.direct_url
                ):
                    progress.set_stage("preparing", "Đang làm mới link TikTok")
                    item = await asyncio.to_thread(
                        _probe_media_sync,
                        item.platform,
                        item.url,
                        processing_limit,
                    )
                    self.item = item
                temp_dir = tempfile.mkdtemp(prefix="peto-media-")

                if item.media_kind == "photo":
                    downloaded_files = await asyncio.to_thread(
                        _download_images_sync,
                        item,
                        upload_limit,
                        temp_dir,
                        progress,
                    )
                    platform_label = PLATFORM_LABELS.get(item.platform, "Media")
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
                        progress.set_stage(
                            "uploading",
                            f"Lượt {batch_index}/{len(batches)}",
                        )
                        uploads = [
                            discord.File(path, filename=filename)
                            for path, filename in batch
                        ]
                        try:
                            if batch_index == 1:
                                await stop_progress_tracker()
                                await interaction.edit_original_response(
                                    content=(
                                        f"✅ Ảnh {platform_label} đã sẵn sàng "
                                        f"({batch_index}/{len(batches)}):"
                                    ),
                                    attachments=uploads,
                                )
                            else:
                                await interaction.followup.send(
                                    (
                                        f"✅ Ảnh {platform_label} của bạn đã sẵn sàng "
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
                    processing_limit,
                    temp_dir,
                    progress,
                )

                file_size = os.path.getsize(filepath)
                if file_size > upload_limit:
                    if not gateway_enabled:
                        raise MediaDownloadError(
                            "File vượt giới hạn upload của Discord và Download Gateway "
                            "chưa được kết nối Cloudflare."
                        )
                    try:
                        progress.set_stage("publishing")
                        public_url = await gateway.publish_file(
                            filepath,
                            display_name=filename,
                            owner_id=user_id,
                        )
                    except Exception as error:
                        from features.download_gateway import DownloadGatewayError

                        if isinstance(error, DownloadGatewayError):
                            raise MediaDownloadError(str(error)) from error
                        raise
                    await stop_progress_tracker()
                    await interaction.edit_original_response(
                        content=(
                            "✅ File lớn đã sẵn sàng tải bên ngoài Discord:\n"
                            f"<{public_url}>\n"
                            "-# Link riêng tư, hết hạn sau 2 giờ; đừng chia sẻ cho người khác."
                        ),
                        attachments=[],
                    )
                    return

                progress.set_stage("uploading")
                upload = discord.File(filepath, filename=filename)
                try:
                    await stop_progress_tracker()
                    await interaction.edit_original_response(
                        content="✅ File của bạn đã sẵn sàng:",
                        attachments=[upload],
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
            await stop_progress_tracker()
            await interaction.edit_original_response(
                content=f"❌ {error}",
                attachments=[],
            )
        except Exception:
            logger.exception(
                "Universal Media Downloader thất bại (platform=%s, user=%s)",
                self.item.platform,
                user_id,
            )
            await stop_progress_tracker()
            await interaction.edit_original_response(
                content="❌ Không thể chuẩn bị file lúc này. Hãy thử lại sau nhé.",
                attachments=[],
            )
        finally:
            await stop_progress_tracker()
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
    @app_commands.describe(
        link="Link YouTube, TikTok video/photo hoặc X video/ảnh/GIF",
        format="Định dạng; auto sẽ tự nhận diện nội dung phù hợp",
    )
    async def download_command(
        self,
        interaction: discord.Interaction,
        link: str,
        format: Literal["auto", "mp3", "mp4"] = "auto",
    ) -> None:
        url = _clean_url(link)
        platform = _platform_from_url(url)
        if not platform:
            return await interaction.response.send_message(
                "❌ Chỉ hỗ trợ link YouTube, TikTok và X/Twitter.",
                ephemeral=True,
            )
        if platform != "youtube" and format == "mp3":
            return await interaction.response.send_message(
                "❌ Tùy chọn MP3 hiện chỉ áp dụng cho YouTube. "
                "TikTok/X sẽ tải video, ảnh hoặc GIF theo nội dung link.",
                ephemeral=True,
            )
        requested_format = (
            "mp3" if platform == "youtube" and format == "auto" else
            "mp4" if platform != "youtube" and format == "auto" else
            format
        )

        await interaction.response.defer(thinking=True, ephemeral=True)
        is_tiktok_photo = bool(_tiktok_photo_id(url))
        try:
            async with self.probe_semaphore:
                upload_limit = int(
                    getattr(interaction, "filesize_limit", None) or DEFAULT_UPLOAD_LIMIT
                )
                gateway = self.bot.get_cog("DownloadGateway")
                probe_limit = (
                    max(upload_limit, int(getattr(gateway, "max_file_bytes", 0)))
                    if gateway and getattr(gateway, "enabled", False)
                    else upload_limit
                )
                if is_tiktok_photo:
                    item = await asyncio.to_thread(_probe_tiktok_photo_sync, url)
                elif platform == "x":
                    item = await asyncio.to_thread(_probe_x_special_media_sync, url)
                    if item is None:
                        item = await asyncio.to_thread(
                            _probe_media_sync,
                            platform,
                            url,
                            probe_limit,
                            requested_format,
                        )
                else:
                    item = await asyncio.to_thread(
                        _probe_media_sync,
                        platform,
                        url,
                        probe_limit,
                        requested_format,
                    )
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
