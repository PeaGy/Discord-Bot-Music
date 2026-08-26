from __future__ import annotations

import re

import discord


SIN_EMOJIS = {
    "wrath": "<:wrath:1537166014997991534>",
    "lust": "<:lust:1537165950732861624>",
    "sloth": "<:sloth:1537165914460655687>",
    "gluttony": "<:gluttony:1537165803214868611>",
    "gloom": "<:gloom:1537165838728306778>",
    "pride": "<:pride:1537165985931591760>",
    "envy": "<:envy:1537165879215919134>",
}

STATUS_EMOJIS = {
    "bleed": "<:bleed:1537164734556545037>",
    "burn": "<:burn:1537164682798833764>",
    "charge": "<:charge:1537164950902935592>",
    "poise": "<:poise:1537164779423268954>",
    "rupture": "<:rupture:1537164865368621106>",
    "sinking": "<:sinking:1537164909970722847>",
    "speed": "<:speed:1537164265750929448>",
    "tremor": "<:tremor:1537164820544102401>",
    "bind": "<:bind:1537193976606752881>",
    "haste": "<:haste:1537194202033688686>",
    "ammo": "<:ammo:1537193941177344052>",
}

SPECIAL_STATUS_EMOJIS = (
    # Cụm dài phải đứng trước cụm ngắn để không chèn hai badge vào cùng status.
    (r"Slash Resist Down", "<:slash_resist_down:1537203450968801391>"),
    (r"Slash Protection", "<:slash_protection:1537203118763409448>"),
    (r"Slash Power Up", "<:slash_power_up:1537203232252895334>"),
    (r"Slash Power Down", "<:slash_power_down:1537203364876656811>"),
    (r"Slash Fragility", "<:slash_fragility:1537203270605475900>"),
    (r"Slash DMG Up", "<:slash_dmg_up:1537203182432948465>"),
    (r"Slash DMG Down", "<:slash_dmg_down:1537203316596154488>"),
    (r"Pierce Resist Down", "<:pierce_resist_down:1537203658801025024>"),
    (r"Pierce Protection", "<:pierce_protection:1537203945779503184>"),
    (r"Pierce Power Up", "<:pierce_power_up:1537203848181981265>"),
    (r"Pierce Power Down", "<:pierce_power_down:1537203717172895910>"),
    (r"Pierce Fragility", "<:pierce_fragility:1537203802942218350>"),
    (r"Pierce DMG Up", "<:pierce_dmg_up:1537203905664917525>"),
    (r"Pierce DMG Down", "<:pierce_dmg_down:1537203769672999065>"),
    (r"Blunt Resist Down", "<:blunt_resist_down:1537204198272401521>"),
    (r"Blunt Protection", "<:blunt_protection:1537204523435696218>"),
    (r"Blunt Power Up", "<:blunt_power_up:1537204404237901899>"),
    (r"Blunt Power Down", "<:blunt_power_down:1537204266316464280>"),
    (r"Blunt Fragility", "<:blunt_fragility:1537204353172115496>"),
    (r"Blunt DMG Up", "<:blunt_dmg_up:1537204471577313291>"),
    (r"Blunt DMG Down", "<:blunt_dmg_down:1537204314005700678>"),
    (r"Offense Level Down", "<:offense_level_down:1537193594652463104>"),
    (r"Defense Level Down", "<:defense_level_down:1537193661929099264>"),
    (r"Offense Level Up", "<:offense_level_up:1537194106558881833>"),
    (r"Defense Level Up", "<:defense_level_up:1537194032436871284>"),
    (r"Defense Power Up", "<:defense_power_up:1537205901365354506>"),
    (r"Defense Power Down", "<:defense_power_down:1537205435843485807>"),
    (r"Attack Power Up", "<:attack_power_up:1537205849024368690>"),
    (r"Attack Power Down", "<:attack_power_down:1537205385860223076>"),
    (r"Clash Power Up", "<:clash_power_up:1537205950157422743>"),
    (r"Clash Power Down", "<:clash_power_down:1537205479674220614>"),
    (r"Base Power Up", "<:base_power_up:1537206000850047026>"),
    (r"Plus Coin Boost", "<:plus_coin_boost:1537201107741184001>"),
    (r"Plus Coin Drop", "<:plus_coin_drop:1537201184534962317>"),
    (r"Minus Coin Boost", "<:minus_coin_boost:1537201245683458119>"),
    (r"Minus Coin Drop", "<:minus_coin_drop:1537201310363820092>"),
    (r"Charge Barrier", "<:charge_barrier:1537200681725988955>"),
    (r"Savage Tigermark Round", "<:savage_tigermark_round:1537195638955974718>"),
    (r"Tigermark Round", "<:tigermark_round:1537195599257141279>"),
    (
        r"The Living\s+(?:and|&)\s+The Departed",
        "<:the_living_the_departed:1537195517602435142>",
    ),
    (r"The Living", "<:the_living_the_departed:1537195517602435142>"),
    (r"The Departed", "<:the_living_the_departed:1537195517602435142>"),
    (r"Magic Bullet", "<:magic_bullet:1537195372139516015>"),
    (
        r"Bullet(?:\s+of\s+|\s*[-–—:]\s*)Solitude",
        "<:bullet_solitude:1537195423519744123>",
    ),
    (r"Shin\s*\(心\)", "<:shin:1537195691384774766>"),
    (r"Butterfl(?:y|ies)", "<:butterfly:1537195556760330260>"),
    (r"Paralyze", "<:paralyze:1537200716580659283>"),
    (r"Aggro", "<:aggro:1537201349828022272>"),
    (r"Damage Up", "<:damage_up:1537205765591269396>"),
    (r"Damage Down", "<:damage_down:1537205512481935380>"),
    (r"Power Up", "<:power_up:1537205804749295731>"),
    (r"Power Down", "<:power_down:1537205340767264918>"),
    (r"Fragile", "<:fragile:1537206028754624622>"),
)

COMBAT_EMOJIS = {
    "defense": "<:defense:1537176359640498327>",
    "hp": "<:hp:1537176333472497694>",
    "pierce": "<:pierce:1537176269773479966>",
    "slash": "<:slash:1537176233517781093>",
    "blunt": "<:blunt:1537176623164694658>",
    "offense": "<:offense:1537177450063069224>",
    "evade": "<:evade:1537176298730946700>",
    "counter": "<:counter:1537200744409727097>",
}

COIN_EMOJIS = {
    "coin": "<:coin:1537178958154436739>",
    "unbreakable_coin": "<:unbreakable_coin:1537179018573520917>",
}

IDENTITY_RARITY_EMOJIS = {
    "1": "<:IDNumber1:1537226566507962449>",
    "2": "<:IDNumber2:1537226521830367292>",
    "3": "<:IDNumber3:1537226464364339220>",
}

EGO_RISK_EMOJIS = {
    "zayin": "<:ZAYIN:1537226615325458563>",
    "teth": "<:TETH:1537226660972331080>",
    "he": "<:HE:1537226707160010793>",
    "waw": "<:WAW:1537226749861957744>",
}

# Discord không hỗ trợ tô màu từng đoạn text. Màu viền embed là cách biểu diễn
# Sin Affinity rõ và ổn định nhất trên cả desktop lẫn mobile.
SIN_COLORS = {
    "wrath": 0xD33A2C,
    "lust": 0xE87924,
    "sloth": 0xD6B21E,
    "gluttony": 0x65A83E,
    "gloom": 0x3C9AA8,
    "pride": 0x2767A8,
    "envy": 0x8246A8,
    "none": 0x5865F2,
}


def _add_status_emojis(text: str) -> str:
    value = str(text or "")
    protected: dict[str, str] = {}
    for pattern, emoji in SPECIAL_STATUS_EMOJIS:
        def replace_special(match: re.Match, *, badge: str = emoji) -> str:
            token = f"\ue000{len(protected)}\ue001"
            protected[token] = f"{match.group(0)} {badge}"
            return token

        value = re.sub(
            rf"(?<!\w)({pattern})(?!\w)(?!\s*<:)",
            replace_special,
            value,
            flags=re.IGNORECASE,
        )
    # Offense/Defense không nằm trong danh sách tự thay thế: chúng chỉ có ý
    # nghĩa status khi thuộc trọn cụm Level Up/Down ở trên. Badge Defense tổng
    # quan và damage type vẫn được renderer gắn thủ công ở đúng vị trí.
    automatic_combat_emojis = {
        key: emoji
        for key, emoji in COMBAT_EMOJIS.items()
        if key not in {"offense", "defense", "counter"}
    }
    for term, emoji in {**STATUS_EMOJIS, **automatic_combat_emojis}.items():
        value = re.sub(
            rf"\b{re.escape(term)}\b(?!\s*<:)",
            lambda match: f"{match.group(0)} {emoji}",
            value,
            flags=re.IGNORECASE,
        )
    for token, rendered in protected.items():
        value = value.replace(token, rendered)
    # Unbreakable Coin là một loại Coin đặc biệt nên giữ badge riêng trong effect.
    # Coin thường chỉ dùng emoji ở dòng stats và tiêu đề từng Coin bên dưới;
    # không thay mọi chữ "Coin" trong câu vì sẽ làm Coin Power/[Coin Start]/
    # "this Coin's damage" trở nên rối mắt.
    value = re.sub(
        r"\bUnbreakable Coins?\b(?!\s*<:)",
        lambda match: f"{match.group(0)} {COIN_EMOJIS['unbreakable_coin']}",
        value,
        flags=re.IGNORECASE,
    )
    return value


def _skill_badges(label: str, skill_type: str) -> str:
    """Emoji loại đòn ở tiêu đề; Defense có cả badge vai trò và loại thủ."""
    badges: list[str] = []
    if str(label or "").casefold() == "defense":
        badges.append(COMBAT_EMOJIS["defense"])
    normalized_type = str(skill_type or "").casefold()
    for damage_type in ("slash", "pierce", "blunt", "evade", "counter"):
        if damage_type in normalized_type:
            badges.append(COMBAT_EMOJIS[damage_type])
            break
    return " ".join(badges)


def _truncate(text: str, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _sin_style(sin: str) -> tuple[str, int]:
    key = str(sin or "None").casefold()
    return SIN_EMOJIS.get(key, "⚪"), SIN_COLORS.get(key, SIN_COLORS["none"])


def build_identity_kit_embeds(kit: dict) -> list[discord.Embed]:
    """Render full kit thành card màu theo Sin, không làm mất passive dài."""
    embeds: list[discord.Embed] = []
    resistance_lines: list[str] = []
    for resistance in kit.get("resistances") or []:
        damage_type = str(resistance.get("type") or "Unknown")
        damage_emoji = COMBAT_EMOJIS.get(damage_type.casefold(), "▫️")
        resistance_lines.append(
            f"{damage_emoji} **{damage_type}:** "
            f"`{resistance.get('rating') or '?'}` "
            f"`[{resistance.get('multiplier') or '?'}]`"
        )
    resistance_text = "\n".join(resistance_lines) or "Chưa có dữ liệu."
    if kit.get("display_mode") != "single_skill":
        overview = discord.Embed(
            title=str(kit.get("title") or "Limbus Identity Kit"),
            url=str(kit.get("url") or "") or None,
            description=(
                f"{COMBAT_EMOJIS['hp']} **HP:** `{kit.get('hp') or '?'}`\n"
                f"{STATUS_EMOJIS['speed']} **Speed:** `{kit.get('speed') or '?'}`\n"
                f"{COMBAT_EMOJIS['defense']} **Defense Level:** "
                f"`{kit.get('defense_level') or '?'}`\n\n"
                f"**Resistances**\n{resistance_text}\n\n"
                "Thông số theo **Uptie cao nhất** đang có trên wiki. "
                "Nhấn tiêu đề để mở trang nguồn."
            ),
            color=0x2B2D31,
        )
        if kit.get("asset_url"):
            overview.set_thumbnail(url=str(kit["asset_url"]))
        overview.set_footer(text="Màu viền và emoji của mỗi card biểu thị Sin Affinity")
        embeds.append(overview)

    for skill in kit.get("skills") or []:
        sin = str(skill.get("sin") or "None")
        sin_emoji, color = _sin_style(sin)
        combat_badges = _skill_badges(skill.get("label", ""), skill.get("type", ""))
        coin_kinds = list(skill.get("coin_kinds") or [])
        unbreakable_count = coin_kinds.count("unbreakable")
        normal_count = max(0, int(skill.get("coins") or 0) - unbreakable_count)
        if unbreakable_count and normal_count:
            coin_stat = (
                f"**Coin Power:** `{skill.get('coin_power') or '—'}` • "
                f"{COIN_EMOJIS['coin']} × `{normal_count}` • "
                f"{COIN_EMOJIS['unbreakable_coin']} × `{unbreakable_count}`"
            )
        else:
            coin_emoji = (
                COIN_EMOJIS["unbreakable_coin"]
                if unbreakable_count
                else COIN_EMOJIS["coin"]
            )
            coin_stat = (
                f"**Coin {coin_emoji}:** "
                f"`{skill.get('coin_power') or '—'}` × `{skill.get('coins') or 0}`"
            )
        stats = [f"**Base:** `{skill.get('base_power') or '?'}`", coin_stat]
        if skill.get("attack_weight"):
            stats.append(f"**Atk Weight:** `{skill['attack_weight']}`")
        description = " • ".join(stats)
        effects = list(skill.get("effects") or [])
        if effects:
            description += "\n\n" + _add_status_emojis("\n".join(effects))
        embed = discord.Embed(
            title=(
                f"{skill.get('label') or 'Skill'} — "
                f"{skill.get('name') or 'Unknown'} "
                f"{combat_badges} {sin_emoji}"
            ),
            description=_truncate(description, 4000),
            color=color,
        )
        if skill.get("type"):
            embed.set_author(name=f"{sin} • {skill['type']}")
        if kit.get("display_mode") == "single_skill" and kit.get("url"):
            embed.url = str(kit["url"])
            embed.set_footer(text=f"Nguồn: {kit.get('title') or 'Limbus Company Wiki'}")
            if kit.get("asset_url"):
                embed.set_thumbnail(url=str(kit["asset_url"]))
        for coin in skill.get("coin_effects") or []:
            coin_emoji = (
                COIN_EMOJIS["unbreakable_coin"]
                if coin.get("kind") == "unbreakable"
                else COIN_EMOJIS["coin"]
            )
            embed.add_field(
                name=f"{coin_emoji} Coin {coin.get('coin')}",
                value=_truncate(_add_status_emojis(coin.get("effect", "")), 1024),
                inline=False,
            )
        embeds.append(embed)

    # Mỗi passive có card riêng. Nếu gộp thành field, Discord sẽ cắt effect ở
    # giới hạn 1024 ký tự và tái diễn đúng lỗi "wiki bị thiếu" trước đây.
    for passive in kit.get("passives") or []:
        sin = str(passive.get("sin") or "None")
        sin_emoji, color = _sin_style(sin)
        requirement = str(passive.get("requirement") or "").strip()
        heading = f"Passive — {passive.get('name') or 'Unknown'} {sin_emoji}"
        description = _add_status_emojis(passive.get("effect", ""))
        if requirement:
            description = f"**Requirement:** `{requirement}`\n\n{description}"
        embeds.append(
            discord.Embed(
                title=_truncate(heading, 256),
                description=_truncate(description, 4000),
                color=color,
            )
        )

    return embeds


def build_ego_embeds(ego: dict) -> list[discord.Embed]:
    """Render one E.G.O with an overview plus Awakening/Corrosion cards."""
    affinity = str(ego.get("affinity") or "None")
    sin_emoji, color = _sin_style(affinity)
    cost_text = " • ".join(
        f"{_sin_style(item.get('sin', 'None'))[0]} `{item.get('amount')}`"
        for item in ego.get("costs") or []
    ) or "Chưa có dữ liệu"
    resistance_text = "\n".join(
        f"{_sin_style(item.get('sin', 'None'))[0]} **{item.get('sin')}:** "
        f"`{item.get('rating') or '?'}` `[{item.get('multiplier') or '?'}]`"
        for item in ego.get("resistances") or []
    ) or "Chưa có dữ liệu."
    info = [
        f"**Sinner:** {ego.get('sinner') or '?'}",
        f"**Risk Level:** `{ego.get('risk') or '?'}` • **Affinity:** {affinity} {sin_emoji}",
        f"**Sanity:** Awakening `{ego.get('awakening_sanity') or '?'}` • "
        f"Corrosion `{ego.get('corrosion_sanity') or '?'}`",
        f"**E.G.O Resources:** {cost_text}",
    ]
    if ego.get("season"):
        info.append(f"**Season:** {ego['season']}")
    if ego.get("obtained"):
        info.append(f"**Obtained:** {ego['obtained']}")
    if ego.get("abnormality"):
        info.append(f"**Abnormality:** {ego['abnormality']}")
    info.append(f"\n**Sin Resistances**\n{resistance_text}")
    overview = discord.Embed(
        title=f"{ego.get('name') or ego.get('title') or 'Limbus E.G.O'} — {ego.get('sinner') or ''}",
        url=str(ego.get("url") or "") or None,
        description=_truncate("\n".join(info), 4000),
        color=color,
    )
    if ego.get("asset_url"):
        overview.set_thumbnail(url=str(ego["asset_url"]))
    for skill in ego.get("skills") or []:
        coin_kinds = list(skill.get("coin_kinds") or [])
        unbreakable_count = coin_kinds.count("unbreakable")
        normal_count = max(0, int(skill.get("coins") or 0) - unbreakable_count)
        coin_parts: list[str] = []
        if normal_count:
            coin_parts.append(f"{COIN_EMOJIS['coin']} × `{normal_count}`")
        if unbreakable_count:
            coin_parts.append(
                f"{COIN_EMOJIS['unbreakable_coin']} × `{unbreakable_count}`"
            )
        overview.add_field(
            name=f"{skill.get('label') or 'E.G.O Skill'} — {skill.get('name') or 'Unknown'}",
            value=(
                f"**Base:** `{skill.get('base_power') or '?'}` • "
                f"**Coin Power:** `{skill.get('coin_power') or '—'}` • "
                f"{' • '.join(coin_parts) or 'Không có Coin'} • "
                f"**Atk Weight:** `{skill.get('attack_weight') or '?'}`\n"
                "Chi tiết từng hiệu ứng nằm ở card ngay bên dưới."
            ),
            inline=False,
        )
    overview.set_footer(text="Thông số theo Threadspin cao nhất đang có trên wiki")

    # Reuse the battle-card renderer so Coin type, damage/status emoji and long
    # passive handling stay identical between Identity and E.G.O.
    battle_cards = build_identity_kit_embeds({
        "title": ego.get("title"),
        "url": ego.get("url"),
        "display_mode": "single_skill",
        "skills": ego.get("skills") or [],
        "passives": ego.get("passives") or [],
    })
    # Discord may deduplicate rich embeds that share the same URL inside one
    # message. The overview keeps the source link; skill cards remain distinct
    # and use their footer as source attribution.
    for card in battle_cards:
        card.url = None
    return [overview, *battle_cards]


def build_ego_roster_embed(roster: dict) -> discord.Embed:
    entries = list(roster.get("entries") or [])
    lines = [
        f"{index}. **[{entry.get('name') or 'Unknown'}]({entry.get('url')})** "
        f"{EGO_RISK_EMOJIS.get(str(entry.get('risk') or '').casefold(), '')}".rstrip()
        for index, entry in enumerate(entries, start=1)
    ]
    embed = discord.Embed(
        title=f"E.G.O của {roster.get('sinner') or 'Sinner'}",
        url=str(roster.get("source") or "") or None,
        description=_truncate("\n".join(lines) or "Chưa tìm thấy E.G.O.", 4000),
        color=0x2B2D31,
    )
    embed.set_footer(text=f"Tìm thấy {len(entries)} E.G.O trong dữ liệu wiki đã đồng bộ")
    return embed


def build_identity_roster_embed(roster: dict) -> discord.Embed:
    entries = list(roster.get("entries") or [])
    lines = [
        f"{index}. **[{entry.get('name') or 'Unknown'}]({entry.get('url')})** "
        f"{IDENTITY_RARITY_EMOJIS.get(str(entry.get('rarity') or ''), '')}".rstrip()
        for index, entry in enumerate(entries, start=1)
    ]
    embed = discord.Embed(
        title=f"Identity của {roster.get('sinner') or 'Sinner'}",
        url=str(roster.get("source") or "") or None,
        description=_truncate("\n".join(lines) or "Chưa tìm thấy Identity.", 4000),
        color=0x2B2D31,
    )
    embed.set_footer(
        text=f"Tìm thấy {len(entries)} Identity trong dữ liệu wiki đã đồng bộ"
    )
    return embed


async def setup(bot) -> None:
    """Entry point rỗng vì bot tự nạp mọi module trong ``features/``.

    File này chỉ cung cấp helper dựng embed, không đăng ký Cog hay command nào.
    Có entry point vẫn cho phép discord.py nạp nó như một extension an toàn.
    """
    return None
