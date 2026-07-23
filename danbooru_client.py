import aiohttp

BASE_URL = "https://danbooru.donmai.us"
USER_AGENT = "DiscordMusicBot/1.0"
_SAFE_RATINGS = {"g", "general"}

def _force_rating_tags(user_tags: str, tier: str) -> str:
    """Ép tag rating dựa theo cấp độ lệnh (safe, ecchi, explicit)."""
    tags = user_tags.split() if user_tags else []
    
    # Dọn dẹp các tag rating cũ user tự gõ để tránh xung đột
    tags = [t for t in tags if not t.lower().startswith("rating:")]
    
    # Phân loại 3 cấp độ
    if tier == "safe":
        tags.append("rating:g") # Mức 1: Thuần khiết
    elif tier == "ecchi":
        tags.append("rating:s,q") # Mức 2: Gợi cảm & Mập mờ
    elif tier == "explicit":
        tags.append("rating:e") # Mức 3: 18+ hạng nặng
        
    return " ".join(tags)


async def search_posts(tags: str = "", limit: int = 1, random: bool = True, rating_tier: str = "safe") -> list[dict]:
    """Tìm bài post. rating_tier có thể là: 'safe', 'ecchi', 'explicit'"""
    
    final_tags = _force_rating_tags(tags, rating_tier)
        
    if random and "order:" not in final_tags:
        final_tags += " order:random"

    params = {"tags": final_tags, "limit": str(limit)}
    headers = {"User-Agent": USER_AGENT}

    # Bỏ qua chứng chỉ SSL để tránh lỗi Certificate cũ
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        async with session.get(f"{BASE_URL}/posts.json", params=params) as resp:
            if resp.status != 200:
                print(f"🚨 LỖI DANBOORU: Status code {resp.status} cho từ khoá: {final_tags}")
                return []
            data = await resp.json()
            return data or []


async def get_post_by_id(post_id: int) -> dict | None:
    """Lấy 1 bài post theo ID cụ thể."""
    headers = {"User-Agent": USER_AGENT}
    
    # Bổ sung TCPConnector với tham số ssl=False
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        async with session.get(f"{BASE_URL}/posts/{post_id}.json") as resp:
            if resp.status != 200:
                return None
            return await resp.json()


def is_safe_rating(post: dict) -> bool:
    """Kiểm tra 1 post có thuộc rating an toàn (general) hay không."""
    return str(post.get("rating", "")).lower() in _SAFE_RATINGS