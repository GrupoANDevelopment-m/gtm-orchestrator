"""
test_text_sanitizer.py — Unit tests for the emoji-stripping sanitizer.

Critical because the user requires NO emojis in any UI or LLM output.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add server/ to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from text_sanitizer import (  # noqa: E402
    clean_text, strip_emojis, sanitize_for_ui,
)


class TestStripEmojis:
    def test_strips_simple_emoji(self):
        assert strip_emojis("Hello 👋") == "Hello "

    def test_strips_multiple_emojis(self):
        assert strip_emojis("Hello 👋🎉") == "Hello "

    def test_keeps_ascii(self):
        assert strip_emojis("Hello, World!") == "Hello, World!"

    def test_keeps_portuguese_accents(self):
        assert strip_emojis("Olá, café!") == "Olá, café!"

    def test_keeps_chinese(self):
        assert strip_emojis("中文测试") == "中文测试"

    def test_strips_flag_emojis(self):
        # 🇧🇷 = Brazil flag (2 regional indicators)
        result = strip_emojis("Vamos Brasil 🇧🇷!")
        assert "🇧" not in result and "🇷" not in result
        assert "Vamos Brasil !" == result

    def test_strips_symbols(self):
        # Check marks, crosses, etc.
        assert strip_emojis("OK ✓") == "OK "
        assert strip_emojis("No ✗") == "No "

    def test_strips_variation_selectors(self):
        # VS-16 forces emoji presentation
        result = strip_emojis("Red \u2764\ufe0f heart")
        assert "\ufe0f" not in result

    def test_strips_zwj(self):
        # Family emoji with ZWJ
        result = strip_emojis("Family 👨\u200d👩\u200d👧")
        assert "\u200d" not in result

    def test_keeps_arrows(self):
        # We strip arrows in clean_text (they are emoji-like)
        assert "→" not in clean_text("→ go left")

    def test_strips_rocket(self):
        assert "🚀" not in clean_text("Launch 🚀 now")

    def test_strips_fire(self):
        assert "🔥" not in clean_text("Hot 🔥 deal")


class TestCleanText:
    def test_collapse_exclamation(self):
        assert clean_text("Wow!!!") == "Wow!"

    def test_collapse_question(self):
        assert clean_text("Really???") == "Really?"

    def test_collapse_em_dash(self):
        assert clean_text("a —— b") == "a — b"

    def test_collapse_newlines(self):
        assert clean_text("a\n\n\n\nb") == "a\n\nb"

    def test_combined(self):
        result = clean_text("Hello! 👋🎉  Welcome  🚀")
        assert "👋" not in result
        assert "🎉" not in result
        assert "🚀" not in result
        assert "Welcome" in result

    def test_empty(self):
        assert clean_text("") == ""

    def test_none(self):
        assert clean_text(None) is None


class TestEdgeCases:
    def test_only_emoji(self):
        result = clean_text("🎉🎉🎉")
        assert "🎉" not in result

    def test_mixed_languages(self):
        result = clean_text("Hello 世界 🌍 Olá")
        assert "🌍" not in result
        assert "世界" in result
        assert "Olá" in result

    def test_numbers_preserved(self):
        assert "12345" in clean_text("12345 🎉")

    def test_punctuation_preserved(self):
        text = "Hello, world! How are you?"
        cleaned = clean_text(text)
        assert "Hello, world!" in cleaned
        assert "How are you?" in cleaned

    def test_unicode_preserved(self):
        # Russian
        assert "Привет" in clean_text("Привет 🌍")
        # Arabic
        assert "مرحبا" in clean_text("مرحبا 🌍")
        # Japanese
        assert "こんにちは" in clean_text("こんにちは 🌍")
