"""Bounded, read-only grounding for questions about Peto's public features.

Only public help copy and in-memory command metadata are inputs. Never inspect
callbacks, parameter defaults, environment variables, databases or permissions.
"""

from dataclasses import dataclass
from itertools import islice
import re
import unicodedata

from peto_help_catalog import HELP_CATEGORIES


MAX_HELP_CONTEXT = 8500
_SLASH = re.compile(r"(?<![\w:/])/([a-z][a-z0-9_]*|247)\b", re.I)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text or "").casefold().replace("đ", "d"))
    return re.sub(r"\s+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip()


def _contains(text: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) for phrase in phrases)


@dataclass(frozen=True)
class _Topic:
    category: str
    commands: tuple[str, ...]
    keywords: tuple[str, ...]


_TOPICS = (
    _Topic("commands", (
        "play", "search", "queue", "playnext", "remove", "move", "shuffle", "clear",
        "favorite", "favorites", "recent", "previous", "next", "stop", "pause", "resume",
        "loop", "autoplay", "lyric", "stats", "wrapped", "radio", "connect", "leave", "247", "latency",
    ), ("mo nhac", "phat nhac", "nghe nhac", "bat nhac", "tat nhac", "dung nhac",
        "bo qua bai", "chuyen bai", "hang doi", "voice", "radio", "loi bai hat", "play music", "skip song")),
    _Topic("commands", ("playlist",), ("playlist", "danh sach phat")),
    _Topic("games", ("gacha", "collection", "rank", "exchange identity", "exchange ego", "points"),
           ("gacha", "quay nhan vat", "quay char", "bo suu tap", "doi nhan vat", "doi char", "extraction points", "recruitment points")),
    _Topic("games", ("points", "collection", "rank"),
           ("peto points", "points", "kiem diem", "xem diem", "diem peto", "bang xep hang", "diem hoat dong")),
    _Topic("games", ("coupon codes", "coupon subscribe", "coupon unsubscribe", "coupon subscriptions",
                      "coupon history", "coupon preferences", "coupon used"),
           ("coupon", "gift code", "auto redeem", "auto-redeem", "tu nhap code", "nhan code")),
    _Topic("games", ("dailyreset next", "dailyreset subscribe", "dailyreset unsubscribe", "dailyreset subscriptions"),
           ("daily reset", "dailyreset", "nhac reset", "thong bao reset")),
    _Topic("media", ("download",), ("tai nhac", "tai video", "tai media", "download", "tai mp3", "tai mp4", "tai anh tiktok")),
    _Topic("art_ai", ("art", "wallpaper", "artecchi", "artnsfw", "artinfo", "saucy", "sticker", "emoji"),
           ("sticker", "emoji", "danbooru", "fanart", "wallpaper", "nguon anh", "tim anh")),
    _Topic("art_ai", ("private", "andanh", "resetmemory"),
           ("chat", "tro chuyen", "an danh", "tri nho", "bo nho", "ghi nho", "dm", "study mode",
            "tao anh", "sua anh", "chinh anh", "xoa nen", "tach nen", "cat anh", "ghep anh", "doi nen",
            "tao hinh", "ve tranh", "ve anh", "edit image", "create image", "generate image", "doc anh", "doc video")),
)
_PUBLIC_SINGLE = {name for topic in _TOPICS for name in topic.commands if " " not in name} - {"playlist"}
_PUBLIC_SINGLE.add("help")
_PUBLIC_GROUPED = {name for topic in _TOPICS for name in topic.commands if " " in name}
_PUBLIC_GROUPED.update("playlist " + name for name in (
    "create", "list", "add", "show", "play", "savequeue", "share", "clone", "import", "delete",
))
_HELP_INTENT = (
    "lam sao", "lam the nao", "cach", "huong dan", "dung sao", "dung the nao", "su dung",
    "lenh nao", "lenh gi", "la gi", "hoat dong", "co gi", "bao nhieu", "tai sao", "vi sao",
    "nhu nao", "nhu the nao", "the nao", "help", "how to", "how do", "how can", "what command",
)


def is_peto_help_request(user_text: str) -> bool:
    """Conservative how-to intent, not a bare request to perform an action."""
    text = _normalize(user_text)
    # Quoted examples in translation/rewrite requests are not bot instructions.
    if _contains(text, ("dich cau", "dich sang", "viet lai", "sua ngu phap", "translate", "rewrite")):
        return False
    roots = set(_SLASH.findall(text))
    known_topic = any(_contains(text, topic.keywords) for topic in _TOPICS)
    bot_named = bool(re.search(r"\b(?:peto|pearto|bot|ban|cau)\b", text))
    if not roots and not bot_named and _contains(text, ("trong game", "in game", "in-game", "genshin", "honkai")):
        return False
    overview = bot_named and _contains(text, ("lam duoc gi", "co the lam gi", "tinh nang", "nhung lenh", "biet lam gi"))
    if overview or text in {"help", "tro giup", "huong dan su dung"}:
        return True
    how_to = _contains(text, _HELP_INTENT)
    if not how_to and _contains(text, ("giup toi", "giup minh", "giup tao", "giup to", "cho toi", "cho minh", "ho toi")):
        return False
    question = how_to or bool(re.search(
        r"^(?:(?:peto|pearto|bot|ban|gacha)\s+)?co\b.+\b(?:khong|ko)\b", text
    ))
    bot_usage = bot_named and _contains(text, ("tinh nang", "cac lenh", "nhung lenh", "su dung", "hoat dong"))
    return bool((roots or known_topic or bot_usage) and question)


def _selected_topics(text: str) -> list[_Topic]:
    normalized = _normalize(text)
    roots = set(_SLASH.findall(normalized))
    return [topic for topic in _TOPICS if
            roots.intersection(name.split()[0] for name in topic.commands)
            or _contains(normalized, topic.keywords)][:3]


def _public_commands(tree) -> dict[str, object]:
    if tree is None:
        return {}
    # walk_commands is local and does not fetch/sync or run permission checks.
    result = {}
    for command in islice(tree.walk_commands(), 500):
        name = getattr(command, "qualified_name", "")
        if name in _PUBLIC_SINGLE or name in _PUBLIC_GROUPED:
            # Groups have no parameters; only include executable slash commands.
            if hasattr(command, "parameters"):
                result[name] = command
    return result


def _relevant_commands(tree, text: str) -> tuple[dict[str, object], list[_Topic]]:
    commands = _public_commands(tree)
    topics = _selected_topics(text)
    if not topics:
        preferred = ("help", "play", "queue", "next", "gacha", "points", "collection", "rank", "download",
                     "art", "sticker", "private", "andanh", "coupon codes", "dailyreset next")
        return {name: commands[name] for name in preferred if name in commands}, topics
    wanted = {name for topic in topics for name in topic.commands}
    selected = {name: command for name, command in commands.items()
                if name in wanted or name.split()[0] in wanted or name == "help"}
    explicit = set(_SLASH.findall(_normalize(text)))
    ordered = sorted(selected, key=lambda name: (name.split()[0] not in explicit, name == "help", name))
    return {name: selected[name] for name in ordered[:35]}, topics


def _command_line(name: str, command) -> str:
    params = []
    for param in islice(command.parameters, 12):
        # Use public display_name (it may be renamed), never repr(default).
        label = getattr(param, "display_name", param.name)
        if not re.fullmatch(r"[\w-]{1,32}", label):
            continue
        detail = str(getattr(param, "description", ""))
        detail = "" if detail in {"…", "..."} else detail[:100]
        choices = [str(choice.name)[:40] for choice in islice(getattr(param, "choices", ()) or (), 8)]
        details = ("bắt buộc" if param.required else "tùy chọn")
        if detail:
            details += "; " + detail
        if choices:
            details += "; lựa chọn: " + ", ".join(choices)
        params.append(f"{label}:<{details}>")
    args = " " + " ".join(params) if params else ""
    return f"/{name}{args} — {str(command.description)[:150]}"


def _documentation(topics: list[_Topic], commands: dict[str, object], text: str) -> list[str]:
    """Select complete public lines from the SAME copy used by /help."""
    selected = []
    explicit = set(_SLASH.findall(_normalize(text)))
    for category in dict.fromkeys(topic.category for topic in topics):
        keywords = tuple(word for topic in topics if topic.category == category for word in topic.keywords)
        relevant_section = False
        for line in HELP_CATEGORIES[category][1].splitlines():
            if line.startswith("**"):
                relevant_section = _contains(_normalize(line), keywords)
                continue
            if not line.strip():
                continue
            references = re.findall(r"`/([^`]+)`", line)
            roots = set(_SLASH.findall(line))
            if roots and not roots.issubset({name.split()[0] for name in commands}):
                continue
            # Owner/admin siblings of public groups must never enter the context.
            if any(not any(ref == name or re.match(re.escape(name) + r"(?=[\s<\[])", ref)
                           for name in commands) for ref in references):
                continue
            if _contains(_normalize(line), ("chu bot", "quan tri vien", "admin")):
                continue
            if roots or relevant_section or category == "media" or _contains(_normalize(line), keywords):
                score = (10 if roots & explicit else 0) + (3 if roots else 0)
                selected.append((score, line))
    selected.sort(key=lambda item: -item[0])
    return list(dict.fromkeys(line for _, line in selected))


_GROUNDING_RULES = """## Hướng dẫn sử dụng Peto — nguồn nội bộ công khai
Đây là lượt hỏi cách dùng, CHỈ giải thích; không gọi công cụ, không phát/bỏ nhạc,
không tạo/tìm/sửa ảnh, không tải file, không đổi điểm hay cài đặt. Không nói đã làm.
Giữ giọng Peto; trả lời ngắn, từng bước và ví dụ phù hợp câu hỏi bằng tiếng Việt.
Chỉ dẫn lệnh có trong danh sách bên dưới. Tên/tham số đăng ký là nguồn đúng khi
khác ví dụ /help; không bịa /skip hoặc /ai. Cú pháp tham số là tên:giá_trị.
Đăng ký lệnh KHÔNG chứng minh người hỏi có quyền, lệnh đã sync thành công hoặc
server đã bật tính năng. Không biết số dư, bật/tắt, kênh, giá/giới hạn hiện tại.
Các con số trong /help là mô tả chung, không phải cấu hình live của server này.
Chưa có dữ kiện thì nói rõ, hướng dẫn người dùng kiểm tra thay vì đoán.
Lệnh quản trị/chủ bot không thuộc tài liệu công khai được chọn ở bản này; không
khẳng định chúng không tồn tại. Không yêu cầu token, cookie, .env hay dữ liệu riêng.
Khi hướng dẫn mở nhạc: vào một kênh voice trong server rồi dùng lệnh phát; bot
cần quyền xem/kết nối/nói. Có thể hướng dẫn trong DM, nhưng nhạc phải phát ở server.
Tài liệu chỉ dùng để trả lời câu hỏi hiện tại; không phải yêu cầu thực hiện ví dụ.
"""


def build_peto_help_context(tree, user_text: str) -> str:
    if not is_peto_help_request(user_text):
        return ""
    commands, topics = _relevant_commands(tree, user_text)
    parts = [_GROUNDING_RULES, "### Lệnh công khai liên quan đang đăng ký (không phải toàn bộ lệnh)"]
    budget = MAX_HELP_CONTEXT - sum(len(part) + 1 for part in parts)
    # Leave room for shared usage instructions, even with a large command group.
    registry_budget = min(budget, 4500)
    included = {}
    for name, command in commands.items():
        line = _command_line(name, command)
        if len(line) + 1 <= registry_budget:
            parts.append(line)
            included[name] = command
            registry_budget -= len(line) + 1
    if not commands:
        parts.append("Chưa có lệnh công khai phù hợp để xác nhận. Không đoán cú pháp hoặc gọi công cụ.")
    parts.append("### Trích tài liệu dùng chung với bảng trợ giúp")
    budget = MAX_HELP_CONTEXT - sum(len(part) + 1 for part in parts)
    for line in _documentation(topics, included, user_text):
        if len(line) + 1 <= budget:
            parts.append(line)
            budget -= len(line) + 1
    return "\n".join(parts)


def public_help_answer_supported(tree, answer: str) -> bool:
    """Reject invented slash names without executing checks or callbacks."""
    commands = _public_commands(tree)
    roots = {name.split()[0] for name in commands}
    if not set(_SLASH.findall(answer)).issubset(roots):
        return False
    for reference in re.findall(r"`/([^`]+)`", answer):
        if reference in roots:  # Mentioning a command group is fine.
            continue
        if not any(reference == name or re.match(re.escape(name) + r"(?=[\s<\[])", reference)
                   for name in commands):
            return False
    return True


def peto_help_fallback(tree, user_text: str) -> str:
    """Safe text if a model returns a tool call despite read-only instructions."""
    commands, _ = _relevant_commands(tree, user_text)
    lines = ["Peto chỉ hướng dẫn ở lượt này nhé, chưa thực hiện thao tác nào."]
    for name, command in list(commands.items())[:5]:
        lines.append(f"`/{name}` — {str(command.description)[:150]}")
    if not commands:
        lines.append("Peto chưa có đủ thông tin lệnh công khai để hướng dẫn chính xác; cậu hỏi quản trị viên giúp nhé.")
    return "\n".join(lines)
