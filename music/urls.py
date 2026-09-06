"""Pure URL helpers shared by metadata lookup and local audio cache reads."""
import re
from urllib.parse import parse_qs, urlsplit


def canonical_youtube_url(value: str) -> str | None:
    """Identify an explicit video, never a search, playlist or lookalike host."""
    try:
        parsed = urlsplit(str(value).strip())
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        host = (parsed.hostname or "").casefold()
        parts = parsed.path.strip("/").split("/")
        if host in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
            video_id = parts[0]
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [""])[0]
            elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
                video_id = parts[1]
            else:
                return None
        else:
            return None
    except ValueError:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return None
    return f"https://www.youtube.com/watch?v={video_id}"
