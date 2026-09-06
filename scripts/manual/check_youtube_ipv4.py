"""Opt-in metadata probe: direct IPv4, no account cookies or audio download."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ytdlp_support import extract_info_with_retry, youtube_ydl_options


TEST_URL = "https://www.youtube.com/watch?v=zz2a9Q2Wru0"


def probe() -> int:
    options = youtube_ydl_options({
        "format": "bestaudio/best",
        "quiet": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 0,
        "extractor_retries": 0,
        "cachedir": False,
    })
    # An empty proxy explicitly disables environment proxy auto-detection.
    options.update(
        proxy="",
        source_address="0.0.0.0",
        cookiefile=None,
        cookiesfrombrowser=None,
    )
    print("DANG THU: YouTube qua IPv4 truc tiep, khong cookie.", flush=True)
    try:
        info = extract_info_with_retry(
            TEST_URL, options, download=False, attempts=2, retry_delay=1,
        )
        audio_formats = [
            item for item in (info or {}).get("formats", [])
            if item.get("url") and item.get("acodec") not in (None, "none")
        ]
        if not audio_formats:
            raise RuntimeError("Khong tim thay dinh dang audio.")
    except Exception as error:
        print("THU IPv4 THAT BAI:", error)
        return 1

    print("LAY DUOC THONG TIN AUDIO:", info.get("title", "Unknown"))
    print("So dinh dang audio:", len(audio_formats))
    print("Day la kiem tra metadata; chua kiem tra tai audio/phat Discord.")
    return 0


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    return probe()


if __name__ == "__main__":
    raise SystemExit(main())
