"""Six-row block alphabet ("shadowed terminal" style): solid █ bodies with double-line box-drawing shadows.
Only the glyphs the film needs: H E V I + 3 . 4 R."""

FONT = {
    "H": ["██╗  ██╗",
          "██║  ██║",
          "███████║",
          "██╔══██║",
          "██║  ██║",
          "╚═╝  ╚═╝"],
    "E": ["███████╗",
          "██╔════╝",
          "█████╗  ",
          "██╔══╝  ",
          "███████╗",
          "╚══════╝"],
    "V": ["██╗   ██╗",
          "██║   ██║",
          "██║   ██║",
          "╚██╗ ██╔╝",
          " ╚████╔╝ ",
          "  ╚═══╝  "],
    "I": ["██╗",
          "██║",
          "██║",
          "██║",
          "██║",
          "╚═╝"],
    "R": ["██████╗ ",
          "██╔══██╗",
          "██████╔╝",
          "██╔══██╗",
          "██║  ██║",
          "╚═╝  ╚═╝"],
    "3": ["██████╗ ",
          "╚════██╗",
          " █████╔╝",
          " ╚═══██╗",
          "██████╔╝",
          "╚═════╝ "],
    "4": ["██╗  ██╗",
          "██║  ██║",
          "███████║",
          "╚════██║",
          "     ██║",
          "     ╚═╝"],
    "+": ["       ",
          "  ██╗  ",
          "██████╗",
          "╚═██╔═╝",
          "  ╚═╝  ",
          "       "],
    ".": ["   ",
          "   ",
          "   ",
          "   ",
          "██╗",
          "╚═╝"],
}

ROWS = 6
CHARS = "█╗╔╝╚║═"


def letter(ch: str):
    rows = FONT[ch]
    w = max(len(r) for r in rows)
    return [r.ljust(w) for r in rows]


def layout(word: str, spacing: int = 1):
    """Returns list of (char, col_offset, rows) for each letter, and total width in cells."""
    out = []
    x = 0
    for i, ch in enumerate(word):
        rows = letter(ch)
        out.append((ch, x, rows))
        x += len(rows[0]) + (spacing if i < len(word) - 1 else 0)
    return out, x
