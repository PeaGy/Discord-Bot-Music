"""Chuyển LaTeX/MathJax trong câu trả lời của Grok sang text Unicode.

Discord không render LaTeX. `MATH_FORMATTING_PROMPT` đã cấm model xuất
``\\(...\\)``, ``\\frac``, ``\\mathrm{...}``..., nhưng prompt không bảo đảm tuyệt
đối: người dùng vẫn thấy chuỗi thô như ``\\(\\Delta l = 0{,}5\\,\\mathrm{mm}\\)``
(Discord còn nuốt dấu ``\\`` nên nó hiện thành ``(\\Delta l = 0{,}5\\,\\mathrm{mm})``).

Module này là lớp hậu xử lý tất định chạy trước khi text ra Discord. Nguyên tắc:

- Chỉ đổi các lệnh nằm trong danh sách trắng. Lệnh lạ giữ nguyên, nên đường dẫn
  Windows, regex ``\\d+`` hay escape Markdown ``\\*`` không bị phá.
- Bỏ qua code block có tag ngôn ngữ thật (```python, ```latex...). Code block
  không tag hoặc tag ``text``/``math`` vẫn được chuyển, vì prompt yêu cầu đặt
  biến đổi toán học vào ```text.
- Không đụng gì nếu text không chứa dấu hiệu LaTeX nào.
"""

from __future__ import annotations

import re

__all__ = ["contains_latex", "latex_to_discord"]


# `{` `}` do `\{` `\}` sinh ra được giữ tạm bằng ký tự Private Use để bước dọn
# ngoặc cuối cùng không xoá nhầm.
_BRACE_OPEN = ""
_BRACE_CLOSE = ""

_COMMAND_RE = re.compile(r"\\([A-Za-z]+)")

_LATEX_HINT_RE = re.compile(
    r"\\[A-Za-z]{2,}"
    r"|\\[(){}\[\],;:!%$ ]"
    r"|\$\$"
    r"|\{,\}"
    r"|\{\.\}"
    r"|\^\{"
    r"|_\{"
)

_FENCE_BLOCK_RE = re.compile(
    r"(?ms)^[ \t]*```[^\n]*\n.*?(?:^[ \t]*```[ \t]*$|\Z)"
)

# Code block những ngôn ngữ này là mã nguồn thật, không phải toán.
_CODE_LANGUAGES = {
    "asm", "bash", "bat", "c", "c++", "cmake", "cpp", "cs", "csharp", "css",
    "dart", "diff", "dockerfile", "elixir", "erlang", "go", "graphql",
    "groovy", "haskell", "hs", "html", "http", "ini", "java", "javascript",
    "js", "json", "json5", "jsonc", "kotlin", "latex", "lua", "makefile",
    "matlab", "nginx", "objc", "perl", "php", "pl", "powershell", "ps1",
    "prisma", "protobuf", "py", "python", "r", "rb", "regex", "rs", "ruby",
    "rust", "scala", "scss", "sh", "shell", "sql", "svg", "swift", "tex",
    "toml", "ts", "tsx", "typescript", "vb", "vim", "xml", "yaml", "yml",
    "zsh",
}

_SPACING = {",": " ", ";": " ", ":": " ", "!": "", " ": " "}

_FRACTIONS = {"frac", "dfrac", "tfrac", "cfrac"}

# Lệnh chỉ đổi kiểu chữ: giữ lại nội dung bên trong.
_TEXTLIKE = {
    "text", "textrm", "textbf", "textit", "textsf", "texttt", "textnormal",
    "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathnormal",
    "operatorname", "boldsymbol", "bm", "boxed", "mbox", "hbox", "emph",
    "mathcal", "mathfrak", "mathscr",
}

# Lệnh chỉ ảnh hưởng cách trình bày: bỏ hẳn, giữ nguyên phần đi sau.
_DROP = {
    "left", "right", "middle", "big", "bigl", "bigr", "Big", "Bigl", "Bigr",
    "bigg", "biggl", "biggr", "Bigg", "Biggl", "Biggr",
    "displaystyle", "textstyle", "scriptstyle", "limits", "nolimits",
    "smallskip", "medskip", "bigskip", "noindent",
}

_SPACING_WORDS = {
    "quad": " ",
    "qquad": " ",
    "thinspace": " ",
    "enspace": " ",
    "space": " ",
}

# Ký tự kết hợp đặt ngay sau ký tự gốc.
_ACCENTS = {
    "bar": "̄",
    "overline": "̄",
    "vec": "⃗",
    "overrightarrow": "⃗",
    "hat": "̂",
    "widehat": "̂",
    "tilde": "̃",
    "widetilde": "̃",
    "dot": "̇",
    "ddot": "̈",
}

# Tên hàm: in ra dạng chữ bình thường, giữ khoảng trắng phía sau.
_FUNCTIONS = {
    "arccos", "arcsin", "arctan", "arg", "cos", "cosh", "cot", "coth", "csc",
    "deg", "det", "dim", "exp", "gcd", "hom", "inf", "ker", "lg", "lim",
    "liminf", "limsup", "ln", "log", "max", "min", "mod", "bmod", "pmod",
    "Pr", "sec", "sin", "sinh", "sup", "tan", "tanh",
}

_BLACKBOARD = {
    "A": "\U0001d538", "B": "\U0001d539", "C": "ℂ", "D": "\U0001d53b",
    "E": "\U0001d53c", "F": "\U0001d53d", "G": "\U0001d53e", "H": "ℍ",
    "I": "\U0001d540", "J": "\U0001d541", "K": "\U0001d542", "L": "\U0001d543",
    "M": "\U0001d544", "N": "ℕ", "O": "\U0001d546", "P": "ℙ",
    "Q": "ℚ", "R": "ℝ", "S": "\U0001d54a", "T": "\U0001d54b",
    "U": "\U0001d54c", "V": "\U0001d54d", "W": "\U0001d54e", "X": "\U0001d54f",
    "Y": "\U0001d550", "Z": "ℤ",
}

_SYMBOLS = {
    # Chữ Hy Lạp
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "omicron": "ο", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω",
    # Phép toán
    "times": "×", "div": "÷", "cdot": "·", "cdots": "⋯", "ldots": "…",
    "dots": "…", "vdots": "⋮", "ddots": "⋱", "pm": "±", "mp": "∓",
    "ast": "*", "star": "⋆", "bullet": "•", "oplus": "⊕", "ominus": "⊖",
    "otimes": "⊗", "odot": "⊙", "setminus": "\\", "surd": "√",
    # Quan hệ
    "le": "≤", "leq": "≤", "leqslant": "≤", "ge": "≥", "geq": "≥",
    "geqslant": "≥", "ne": "≠", "neq": "≠", "approx": "≈", "equiv": "≡",
    "sim": "∼", "simeq": "≃", "cong": "≅", "propto": "∝", "ll": "≪",
    "gg": "≫", "doteq": "≐",
    # Tập hợp và logic
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "subseteq": "⊆",
    "supset": "⊃", "supseteq": "⊇", "cup": "∪", "cap": "∩",
    "emptyset": "∅", "varnothing": "∅", "forall": "∀", "exists": "∃",
    "nexists": "∄", "neg": "¬", "lnot": "¬", "land": "∧", "wedge": "∧",
    "lor": "∨", "vee": "∨",
    # Hình học
    "perp": "⊥", "parallel": "∥", "angle": "∠", "measuredangle": "∡",
    "triangle": "△", "square": "□", "degree": "°", "prime": "′",
    # Mũi tên
    "to": "→", "rightarrow": "→", "longrightarrow": "⟶", "Rightarrow": "⇒",
    "Longrightarrow": "⟹", "implies": "⇒", "leftarrow": "←",
    "longleftarrow": "⟵", "Leftarrow": "⇐", "leftrightarrow": "↔",
    "Leftrightarrow": "⇔", "iff": "⇔", "mapsto": "↦", "uparrow": "↑",
    "downarrow": "↓", "nearrow": "↗", "searrow": "↘",
    # Toán tử lớn và ký hiệu khác
    "sum": "Σ", "prod": "∏", "coprod": "∐", "int": "∫", "iint": "∬",
    "iiint": "∭", "oint": "∮", "infty": "∞", "partial": "∂", "nabla": "∇",
    "hbar": "ℏ", "ell": "ℓ", "aleph": "ℵ", "Re": "ℜ", "Im": "ℑ",
    "therefore": "∴", "because": "∵", "checkmark": "✓", "dagger": "†",
}

_SUPERSCRIPT = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
    "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "−": "⁻", "=": "⁼",
    "(": "⁽", ")": "⁾", "°": "°",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ", "g": "ᵍ",
    "h": "ʰ", "i": "ⁱ", "j": "ʲ", "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ",
    "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ", "v": "ᵛ",
    "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
}

_SUBSCRIPT = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
    "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "−": "₋", "=": "₌",
    "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ", "l": "ₗ",
    "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ",
    "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}

_ROOT_PREFIX = {"2": "√", "3": "∛", "4": "∜"}

_SIMPLE_TOKEN_RE = re.compile(r"[\w.,°′″]+", re.UNICODE)

_DOLLAR_DISPLAY_RE = re.compile(r"[ \t]*\$\$(.+?)\$\$[ \t]*", re.DOTALL)
_DOLLAR_INLINE_RE = re.compile(r"(?<![$\w])\$(?!\$)([^\n$]{1,300}?)\$(?!\$)")
_MATHY_RE = re.compile(r"[\\^_{}=]")


def contains_latex(text: str) -> bool:
    """Kiểm tra nhanh xem có đáng chạy bộ chuyển đổi không."""
    return bool(_LATEX_HINT_RE.search(str(text or "")))


def _skip_spaces(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t":
        index += 1
    return index


def _read_group(text: str, index: int) -> tuple[str, int] | None:
    """Đọc `{...}` cân bằng bắt đầu tại `index`."""
    index = _skip_spaces(text, index)
    if index >= len(text) or text[index] != "{":
        return None
    depth = 0
    for position in range(index, len(text)):
        char = text[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[index + 1:position], position + 1
    return None


def _read_arg(text: str, index: int) -> tuple[str, int] | None:
    """Đọc một tham số LaTeX: `{...}`, `\\lenh`, hoặc một ký tự đơn."""
    group = _read_group(text, index)
    if group is not None:
        return group
    index = _skip_spaces(text, index)
    if index >= len(text):
        return None
    if text[index] == "\\":
        match = _COMMAND_RE.match(text, index)
        if match:
            return match.group(0), match.end()
        return text[index:index + 2], index + 2
    return text[index], index + 1


def _needs_parens(value: str) -> bool:
    value = value.strip()
    if len(value) <= 1:
        return False
    return not _SIMPLE_TOKEN_RE.fullmatch(value)


def _wrap(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    return f"({value})" if _needs_parens(value) else value


def _map_script(content: str, table: dict[str, str]) -> str | None:
    content = content.strip()
    if not content:
        return None
    mapped = [table.get(char) for char in content]
    if any(item is None for item in mapped):
        return None
    return "".join(mapped)


def _convert_commands(text: str) -> str:
    out: list[str] = []
    index = 0
    length = len(text)
    env_depth = 0

    while index < length:
        char = text[index]
        if char != "\\":
            if char == "&" and env_depth > 0:
                out.append(" ")
            else:
                out.append(char)
            index += 1
            continue

        following = text[index + 1] if index + 1 < length else ""
        if not following:
            out.append(char)
            index += 1
            continue

        if following == "\\":
            # Xuống dòng LaTeX. Chỉ đổi khi phía sau là khoảng trắng/hết chuỗi
            # để không phá đường dẫn UNC dạng `\\server\share`.
            if env_depth or index + 2 >= length or text[index + 2] in " \t\r\n":
                out.append("\n")
                index = _skip_spaces(text, index + 2)
            else:
                out.append("\\\\")
                index += 2
            continue

        if following in _SPACING:
            out.append(_SPACING[following])
            index += 2
            continue
        if following in "()":
            index += 2
            continue
        if following in "[]":
            out.append("\n")
            index = _skip_spaces(text, index + 2)
            continue
        if following in "%$":
            out.append(following)
            index += 2
            continue
        if following == "{":
            out.append(_BRACE_OPEN)
            index += 2
            continue
        if following == "}":
            out.append(_BRACE_CLOSE)
            index += 2
            continue

        match = _COMMAND_RE.match(text, index)
        if not match:
            # Escape Markdown của Discord (`\*`, `\_`, `\~`...) giữ nguyên.
            out.append(text[index:index + 2])
            index += 2
            continue

        name = match.group(1)
        cursor = match.end()

        if name in ("begin", "end"):
            group = _read_group(text, cursor)
            environment = group[0].strip() if group else ""
            if group:
                cursor = group[1]
            if name == "begin":
                if environment in ("array", "tabular"):
                    spec = _read_group(text, cursor)
                    if spec:
                        cursor = spec[1]
                env_depth += 1
            else:
                env_depth = max(0, env_depth - 1)
            out.append("\n")
            index = _skip_spaces(text, cursor)
            continue

        if name in _FRACTIONS:
            first = _read_arg(text, cursor)
            second = _read_arg(text, first[1]) if first else None
            if first and second:
                numerator = _wrap(_convert_commands(first[0]))
                denominator = _wrap(_convert_commands(second[0]))
                out.append(f"{numerator}/{denominator}")
                index = second[1]
                continue

        if name == "sqrt":
            cursor = _skip_spaces(text, cursor)
            root = ""
            if cursor < length and text[cursor] == "[":
                closing = text.find("]", cursor)
                if closing != -1:
                    root = text[cursor + 1:closing].strip()
                    cursor = closing + 1
            argument = _read_arg(text, cursor)
            if argument:
                inner = _wrap(_convert_commands(argument[0]))
                if not root:
                    prefix = "√"
                elif root in _ROOT_PREFIX:
                    prefix = _ROOT_PREFIX[root]
                else:
                    prefix = f"{_map_script(root, _SUPERSCRIPT) or root}√"
                out.append(f"{prefix}{inner}")
                index = argument[1]
                continue

        if name == "mathbb":
            argument = _read_arg(text, cursor)
            if argument:
                inner = _convert_commands(argument[0])
                out.append("".join(_BLACKBOARD.get(c, c) for c in inner))
                index = argument[1]
                continue

        if name in _TEXTLIKE:
            argument = _read_arg(text, cursor)
            if argument:
                out.append(_convert_commands(argument[0]))
                index = argument[1]
                continue

        if name in _ACCENTS:
            argument = _read_arg(text, cursor)
            if argument:
                inner = _convert_commands(argument[0])
                if inner:
                    inner = inner[0] + _ACCENTS[name] + inner[1:]
                out.append(inner)
                index = argument[1]
                continue

        if name in ("hspace", "vspace", "phantom", "label", "tag"):
            argument = _read_arg(text, cursor)
            if argument:
                out.append(" " if name.endswith("space") else "")
                index = argument[1]
                continue

        if name in _SPACING_WORDS:
            out.append(_SPACING_WORDS[name])
            index = cursor
            continue

        if name in _DROP:
            index = cursor
            continue

        if name == "circ":
            tail = "".join(out[-2:])
            out.append("°" if tail.endswith(("^", "^{")) else "∘")
            index = cursor
            continue

        symbol = _SYMBOLS.get(name)
        if symbol is not None:
            out.append(symbol)
            index = cursor
            # LaTeX nuốt đúng một khoảng trắng sau tên lệnh: `\Delta l` là "Δl".
            if (
                index + 1 < length
                and text[index] == " "
                and (text[index + 1].isalnum() or text[index + 1] == "\\")
            ):
                index += 1
            continue

        if name in _FUNCTIONS:
            out.append(name)
            index = cursor
            continue

        # Lệnh lạ: giữ nguyên để không phá nội dung không phải LaTeX.
        out.append(match.group(0))
        index = cursor

    return "".join(out)


_LEFTOVER_BRACE_RE = re.compile(
    r"(\\[A-Za-z]+(?:\{[^{}]*\})*)"
    r"|\{([^{}:\"\n]{0,40})\}"
)

_BARE_SUPERSCRIPT_RE = re.compile(r"[+\-−]?\d+|[A-Za-z]|°")
_BARE_SUBSCRIPT_RE = re.compile(r"\d+")


def _apply_scripts(text: str) -> str:
    out: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if char not in "^_":
            out.append(char)
            index += 1
            continue

        table = _SUPERSCRIPT if char == "^" else _SUBSCRIPT
        group = _read_group(text, index + 1)
        if group is not None:
            content, next_index = group
            mapped = _map_script(content, table)
            out.append(mapped if mapped is not None else f"{char}{_wrap(content)}")
            index = next_index
            continue

        if char == "_":
            # Chỉ nhận chỉ số dưới thuần số, để `user_id`/`snake_case` yên.
            previous = out[-1] if out else ""
            match = _BARE_SUBSCRIPT_RE.match(text, index + 1)
            if not (match and previous and not previous.isspace()):
                out.append(char)
                index += 1
                continue
        else:
            match = _BARE_SUPERSCRIPT_RE.match(text, index + 1)
            if not match:
                out.append(char)
                index += 1
                continue

        mapped = _map_script(match.group(0), table)
        if mapped is None or (match.end() < length and text[match.end()].isalnum()):
            out.append(char)
            index += 1
            continue
        out.append(mapped)
        index = match.end()

    return "".join(out)


def _strip_leftover_braces(text: str) -> str:
    # Chỉ gỡ nhóm ngắn, không chứa dấu hiệu JSON/code, và không gỡ tham số của
    # lệnh lạ (`\unknowncmd{x}` phải ra nguyên vẹn).
    def replace(match: re.Match) -> str:
        if match.group(1) is not None:
            return match.group(1)
        return match.group(2)

    return _LEFTOVER_BRACE_RE.sub(replace, text)


def _tidy(text: str) -> str:
    text = text.replace(_BRACE_OPEN, "{").replace(_BRACE_CLOSE, "}")
    lines = []
    for line in text.split("\n"):
        line = line.rstrip()
        if not line.startswith((" ", "\t")):
            line = re.sub(r"[ \t]{2,}", " ", line)
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def _convert_segment(segment: str) -> str:
    if not contains_latex(segment):
        return segment
    segment = segment.replace("{,}", ",").replace("{.}", ".")
    segment = _DOLLAR_DISPLAY_RE.sub(lambda m: f"\n{m.group(1).strip()}\n", segment)
    segment = _DOLLAR_INLINE_RE.sub(
        lambda m: m.group(1).strip() if _MATHY_RE.search(m.group(1)) else m.group(0),
        segment,
    )
    segment = _convert_commands(segment)
    segment = _apply_scripts(segment)
    segment = _strip_leftover_braces(segment)
    return _tidy(segment)


def _fence_language(block: str) -> str:
    header = block.split("\n", 1)[0].strip().lstrip("`").strip()
    return header.split()[0].lower() if header else ""


def latex_to_discord(text: str) -> str:
    """Đổi công thức LaTeX trong `text` thành dạng Discord hiển thị được."""
    raw = str(text or "")
    if not raw or not contains_latex(raw):
        return raw

    pieces: list[str] = []
    position = 0
    for match in _FENCE_BLOCK_RE.finditer(raw):
        pieces.append(_convert_segment(raw[position:match.start()]))
        block = match.group(0)
        pieces.append(
            block
            if _fence_language(block) in _CODE_LANGUAGES
            else _convert_segment(block)
        )
        position = match.end()
    pieces.append(_convert_segment(raw[position:]))
    return "".join(pieces).strip()
