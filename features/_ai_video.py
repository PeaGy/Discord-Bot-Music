"""Extract video frames and speech audio for Peto's video analysis."""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path


logger = logging.getLogger(__name__)


def _video_ffmpeg_executable(tool: str = "ffmpeg") -> str:
    """Tìm FFmpeg/FFprobe kể cả bản cài qua WinGet chưa nằm trong PATH."""
    configured = os.getenv("FFMPEG_BINARY", "").strip()
    if configured:
        configured_path = Path(configured)
        if tool == "ffmpeg":
            return str(configured_path)
        sibling = configured_path.with_name(f"{tool}{configured_path.suffix}")
        if sibling.is_file():
            return str(sibling)
    detected = shutil.which(tool)
    if detected:
        return detected
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA", "").strip()
        if local_app_data:
            package_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
            candidates = sorted(
                package_root.glob(f"Gyan.FFmpeg*/ffmpeg-*/bin/{tool}.exe"),
                reverse=True,
            )
            if candidates:
                return str(candidates[0])
    return tool


def _run_video_tool(command: list[str], *, tool: str = "ffmpeg") -> str:
    """Chạy công cụ video ngoài event loop và trả stdout để parse metadata."""
    try:
        result = subprocess.run(
            [_video_ffmpeg_executable(tool), *command],
            capture_output=True,
            text=True,
            timeout=150,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Không tìm thấy FFmpeg/FFprobe trên máy chạy bot.") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Video mất quá nhiều thời gian để xử lý.") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "")[-1200:]
        logger.debug("%s xử lý video thất bại: %s", tool, detail)
        raise RuntimeError("FFmpeg không đọc được video này.")
    return result.stdout


def _probe_video_duration(path: str) -> float:
    raw = _run_video_tool(
        [
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            path,
        ],
        tool="ffprobe",
    )
    try:
        payload = json.loads(raw)
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("Không xác định được thời lượng video.") from error
    if duration <= 0:
        raise RuntimeError("Không xác định được thời lượng video.")
    return duration


def extract_video_assets(
    video_path: str,
    directory: str,
    frame_count: int,
    max_seconds: int,
) -> tuple[float, list[tuple[str, float]], str | None]:
    """Lấy các frame theo thứ tự thời gian và audio mono nhỏ để phiên âm."""
    duration = _probe_video_duration(video_path)
    if duration > max_seconds:
        raise ValueError(
            f"Video dài {duration:.0f} giây; Peto hiện chỉ đọc clip tối đa "
            f"{max_seconds} giây trong server này."
        )

    actual_frames = min(frame_count, max(4, int(round(duration * 2))))
    fps = actual_frames / duration
    frame_pattern = os.path.join(directory, "frame_%02d.jpg")
    _run_video_tool([
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", video_path,
        "-vf", (
            f"fps={fps:.8f},"
            "scale='min(1280,iw)':-2:force_original_aspect_ratio=decrease"
        ),
        "-frames:v", str(actual_frames),
        "-q:v", "3",
        frame_pattern,
    ])
    frame_paths = sorted(Path(directory).glob("frame_*.jpg"))
    if not frame_paths:
        raise RuntimeError("Không trích được khung hình nào từ video.")
    frames = [
        (str(path), min(duration, index * duration / len(frame_paths)))
        for index, path in enumerate(frame_paths)
    ]

    audio_path = os.path.join(directory, "speech.mp3")
    try:
        _run_video_tool([
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", video_path,
            "-map", "0:a:0",
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
            audio_path,
        ])
    except RuntimeError:
        audio_path = None
    if audio_path and (
        not os.path.isfile(audio_path) or os.path.getsize(audio_path) <= 0
    ):
        audio_path = None
    return duration, frames, audio_path
