"""
text_sanitizer.py — Strip emojis and problematic Unicode from NIM-generated content.

The user requirement: NO emojis in the UI OR in NIM-generated content.
We keep ASCII + common punctuation + extended Latin + CJK (Chinese/Japanese/Korean)
because users write in many languages including Portuguese and Chinese.

We strip:
  - Emoji blocks (U+1F300..U+1F9FF, U+2600..U+27BF, etc.)
  - Variation selectors (U+FE0F)
  - Zero-width joiners (U+200D)
  - Other pictographic symbols

We also collapse multi-em-dash / multiple exclamation marks (LLM quirk).
"""
from __future__ import annotations

import re
import unicodedata

# Unicode ranges that contain emojis / pictographs
_EMOJI_RANGES = [
    (0x1F300, 0x1F5FF),   # Symbols & pictographs
    (0x1F600, 0x1F64F),   # Emoticons
    (0x1F680, 0x1F6FF),   # Transport & map
    (0x1F700, 0x1F77F),   # Alchemical
    (0x1F780, 0x1F7FF),   # Geometric extended
    (0x1F800, 0x1F8FF),   # Supplemental arrows C
    (0x1F900, 0x1F9FF),   # Supplemental symbols & pictographs
    (0x1FA00, 0x1FA6F),   # Chess symbols
    (0x1FA70, 0x1FAFF),   # Symbols & pictographs extended A
    (0x1FB00, 0x1FBFF),   # Symbols for legacy computing
    (0x2600,  0x26FF),    # Misc symbols
    (0x2700,  0x27BF),    # Dingbats
    (0x2300,  0x23FF),    # Misc technical (partial: clock, gear, etc.)
    (0x2B00,  0x2BFF),    # Misc symbols & arrows
    (0x1F1E6, 0x1F1FF),   # Regional indicator symbols (flags)
]

# Specific characters that are commonly missed by ranges
_EXTRA_EMOJI = {
    "®", "©", "™", "°", "µ", "•", "‣", "⁃",  # footnote / degree etc.
    "❤", "✨", "⭐", "🌟", "💫", "🎉", "🎊",
    "👍", "👎", "👏", "🙌", "👋", "🤝", "🙏",
    "🔥", "💯", "💪", "⚡", "🌈", "☀", "☁", "☂", "☃", "☄",
    "→", "←", "↑", "↓", "⇒", "⇐", "↔", "↕",
    "✓", "✔", "✗", "✘", "✕", "✖",
    "🎯", "🚀", "💡", "📌", "📍", "📎", "📏", "📐",
    "✅", "❌", "❎", "❓", "❔", "❕", "❗", "‼", "⁉",
    "⚙", "⚠", "⚡", "⚪", "⚫", "⭕",
    "▪", "▫", "◾", "◽", "▫",
}


def _is_emoji_char(ch: str) -> bool:
    """Return True if the character is an emoji / pictographic symbol."""
    if ch in _EXTRA_EMOJI:
        return True
    cp = ord(ch)
    for start, end in _EMOJI_RANGES:
        if start <= cp <= end:
            return True
    # Regional indicators (flags), etc. — check via category
    cat = unicodedata.category(ch)
    if cat in ("So", "Sk") and cp > 0x2000:
        # Symbol-other / Symbol-modifier in higher planes
        return True
    return False


# Variation selectors & zero-width joiners
_INVISIBLE = {
    "\ufe0f",  # variation selector-16
    "\ufe0e",  # variation selector-15
    "\u200d",  # ZWJ
    "\u200b",  # ZWSP
    "\u200c",  # ZWNJ
    "\u200e",  # LRM
    "\u200f",  # RLM
    "\ufeff",  # BOM / ZWNBSP
    "\u00ad",  # soft hyphen
}


def strip_emojis(text: str) -> str:
    """Remove all emojis / pictographic symbols from a string.

    Keeps CJK, extended Latin, accented chars (Portuguese etc.).
    """
    if not text:
        return text
    out = []
    for ch in text:
        if ch in _INVISIBLE:
            continue
        if _is_emoji_char(ch):
            continue
        out.append(ch)
    return "".join(out)


def clean_text(text: str) -> str:
    """Full sanitization: strip emojis + collapse excessive punctuation."""
    if not text:
        return text
    text = strip_emojis(text)
    # Collapse multiple exclamation / question marks (LLM habit)
    text = re.sub(r'!{2,}', '!', text)
    text = re.sub(r'\?{2,}', '?', text)
    # Collapse multiple em-dashes
    text = re.sub(r'—{2,}', '—', text)
    # Collapse runs of whitespace left by removed emoji clusters
    text = re.sub(r' {2,}', ' ', text)
    # Collapse 3+ newlines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Trim each line
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def strip_html_emojis(html: str) -> str:
    """Strip emojis from HTML/text that may be rendered to UI."""
    return strip_emojis(html)


# Auto-apply to common NIM-output fields
def sanitize_for_ui(text: str) -> str:
    """The most strict version for UI display."""
    return clean_text(text)


# Tests
if __name__ == "__main__":
    test_cases = [
        ("Hello! 👋 Welcome 🎉", "Hello! Welcome"),
        ("Great job! 🔥💯 Keep it up 🚀", "Great job! Keep it up"),
        ("Vamos lá! 🇧🇷 Brasil ❤️", "Vamos lá! Brasil"),
        ("Tests: ✓ ✔ ✗ ✘", "Tests:"),
        ("Café com pão 🥐☕", "Café com pão"),
        ("中文测试 🎉 表情", "中文测试 表情"),
        ("Math: → ← ⇒ ⇐", "Math:"),
    ]
    for input_text, expected in test_cases:
        result = clean_text(input_text)
        ok = "PASS" if result == expected else "FAIL"
        print(f"[{ok}] '{input_text}' -> '{result}' (expected: '{expected}')")
