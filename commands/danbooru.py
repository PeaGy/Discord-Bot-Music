import discord
from discord import app_commands
from discord.ext import commands

import danbooru_client


def build_embed(post: dict, show_full_tags: bool = False) -> discord.Embed:
    post_id = post.get("id")
    image_url = post.get("large_file_url") or post.get("file_url")
    artist = post.get("tag_string_artist") or "Không rõ"
    source = post.get("source") or f"{danbooru_client.BASE_URL}/posts/{post_id}"

    # Tự động nhận diện màu sắc và cảnh báo theo rating thực tế của ảnh
    rating = str(post.get("rating", "g")).lower()
    if rating in ["e", "explicit"]:
        color = 0xFF0055 # Đỏ cho 18+
        footer_text = "Nguồn: Danbooru • 18+ Hạng nặng (Explicit)"
    elif rating in ["s", "sensitive", "q", "questionable"]:
        color = 0xFF9900 # Cam cho Ecchi
        footer_text = "Nguồn: Danbooru • Gợi cảm (Ecchi)"
    else:
        color = 0x00B8FF # Xanh dương cho SFW
        footer_text = "Nguồn: Danbooru • An toàn (SFW)"

    embed = discord.Embed(
        title=f"Danbooru Post #{post_id}",
        url=f"{danbooru_client.BASE_URL}/posts/{post_id}",
        color=color,
    )
    if image_url:
        embed.set_image(url=image_url)

    embed.add_field(name="Artist", value=artist, inline=True)
    embed.add_field(name="Score", value=str(post.get("score", 0)), inline=True)
    embed.add_field(name="Nguồn", value=source, inline=False)

    if show_full_tags:
        tags = post.get("tag_string", "") # Danbooru dùng tag_string cho toàn bộ tag
        if tags:
            short_tags = ", ".join(tags.split()[:20])
            embed.add_field(name="Tags", value=short_tags, inline=False)

    embed.set_footer(text=footer_text)
    return embed


class RerollView(discord.ui.View):
    """Nút '🎲 Ảnh khác' - tìm lại 1 ảnh khác cùng bộ tags, không cần gõ lại lệnh."""

    def __init__(self, tags: str, rating_tier: str = "safe", timeout: float = 180):
        super().__init__(timeout=timeout)
        self.tags = tags
        self.rating_tier = rating_tier

    @discord.ui.button(label="Ảnh khác", emoji="🎲", style=discord.ButtonStyle.secondary)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        # Gọi lại hàm search với đúng rating_tier mà lệnh ban đầu đã cấp
        posts = await danbooru_client.search_posts(self.tags, limit=1, rating_tier=self.rating_tier)
        if not posts:
            return await interaction.followup.send(
                "❌ Không tìm thấy ảnh nào khớp.", ephemeral=True
            )
            
        embed = build_embed(posts[0])
        await interaction.edit_original_response(embed=embed, view=self)


class Danbooru(commands.Cog):
    """
    Tích hợp Danbooru - Hệ thống 3 cấp độ (SFW, Ecchi, NSFW).
    """

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="art", description="🎨 Tìm ảnh anime")
    @app_commands.describe(tags="Từ khoá tìm kiếm: tên_nhân_vật_(tên_tác_phẩm) or *tennhanvat*")
    async def art(self, interaction: discord.Interaction, tags: str = ""):
        await interaction.response.defer()
        
        # Gọi mức 1: safe
        posts = await danbooru_client.search_posts(tags, limit=1, rating_tier="safe")
        if not posts:
            return await interaction.followup.send("❌ Không tìm thấy ảnh nào khớp.")

        embed = build_embed(posts[0])
        await interaction.followup.send(embed=embed, view=RerollView(tags=tags, rating_tier="safe"))


    @app_commands.command(name="artecchi", description="🥵 Tìm ảnh ecchi")
    @app_commands.describe(tags="Từ khoá tìm kiếm: tên_nhân_vật_(tên_tác_phẩm) or *tennhanvat*")
    async def artecchi(self, interaction: discord.Interaction, tags: str = ""):
        # Bắt buộc kiểm tra kênh chat phải bật chế độ NSFW
        if not interaction.channel.nsfw:
            return await interaction.response.send_message(
                "❌ Lệnh gợi cảm này chỉ được dùng trong kênh **NSFW**!",
                ephemeral=True,
            )

        await interaction.response.defer()
        
        # Gọi mức 2: ecchi
        posts = await danbooru_client.search_posts(tags, limit=1, rating_tier="ecchi")
        if not posts:
            return await interaction.followup.send("❌ Không tìm thấy ảnh nào khớp.")

        embed = build_embed(posts[0])
        await interaction.followup.send(embed=embed, view=RerollView(tags=tags, rating_tier="ecchi"))


    @app_commands.command(name="artnsfw", description="🔞 Tìm ảnh 18+")
    @app_commands.describe(tags="Từ khoá tìm kiếm: tên_nhân_vật_(tên_tác_phẩm) or *tennhanvat*")
    async def artnsfw(self, interaction: discord.Interaction, tags: str = ""):
        # Bắt buộc kiểm tra kênh chat phải bật chế độ NSFW
        if not interaction.channel.nsfw:
            return await interaction.response.send_message(
                "❌ Lệnh 18+ này chỉ được phép sử dụng trong các kênh được bật chế độ **NSFW**!",
                ephemeral=True,
            )

        await interaction.response.defer()
        
        # Gọi mức 3: explicit
        posts = await danbooru_client.search_posts(tags, limit=1, rating_tier="explicit")
        
        if not posts:
            return await interaction.followup.send("❌ Không tìm thấy ảnh nào khớp.")

        embed = build_embed(posts[0])
        await interaction.followup.send(embed=embed, view=RerollView(tags=tags, rating_tier="explicit"))


    @app_commands.command(
        name="wallpaper", description="🖼️ Ảnh chất lượng cao làm hình nền (an toàn)"
    )
    @app_commands.describe(
        huong="Hướng ảnh mong muốn", tags="Từ khoá thêm (không bắt buộc)"
    )
    @app_commands.choices(
        huong=[
            app_commands.Choice(name="Ngang (PC)", value="landscape"),
            app_commands.Choice(name="Dọc (điện thoại)", value="portrait"),
        ]
    )
    async def wallpaper(
        self,
        interaction: discord.Interaction,
        huong: app_commands.Choice[str] = None,
        tags: str = "",
    ):
        await interaction.response.defer()

        dim_filter = ""
        if huong and huong.value == "landscape":
            dim_filter = "width:>=1920 ratio:>=1.5"
        elif huong and huong.value == "portrait":
            dim_filter = "height:>=1920 ratio:<=0.7"

        full_tags = f"{tags} {dim_filter}".strip()
        
        # Wallpaper mặc định luôn dùng mức an toàn (safe)
        posts = await danbooru_client.search_posts(full_tags, limit=1, rating_tier="safe")
        if not posts:
            return await interaction.followup.send(
                "❌ Không tìm thấy ảnh nào khớp kích thước yêu cầu."
            )

        embed = build_embed(posts[0])
        await interaction.followup.send(embed=embed, view=RerollView(tags=full_tags, rating_tier="safe"))


    @app_commands.command(
        name="artinfo", description="🔎 Tra cứu chi tiết 1 bài post theo ID"
    )
    @app_commands.describe(id="ID bài post trên Danbooru")
    async def artinfo(self, interaction: discord.Interaction, id: int):
        await interaction.response.defer()
        post = await danbooru_client.get_post_by_id(id)

        if not post or post.get("id") is None:
            return await interaction.followup.send("❌ Không tìm thấy bài post với ID này.")

        is_safe = danbooru_client.is_safe_rating(post)

        # Kiểm tra rating và channel NSFW
        if not is_safe and not interaction.channel.nsfw:
             return await interaction.followup.send(
                "❌ Bài post này chứa nội dung gợi cảm/18+. Hãy sử dụng lệnh này ở kênh NSFW.",
                ephemeral=True,
            )

        embed = build_embed(post, show_full_tags=True)
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Danbooru(bot))