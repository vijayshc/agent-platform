"""The theme palette has exactly one source of truth: static/css/themes.css.

Theme tokens used to be duplicated in static/js/theme.js, which wrote them inline
on <html> and therefore overrode every stylesheet change (a CSS contrast fix was
dead code). These tests keep the duplication from coming back.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
THEME_JS = REPO / "static" / "js" / "theme.js"
THEMES_CSS = REPO / "static" / "css" / "themes.css"


def test_theme_js_does_not_duplicate_the_palette() -> None:
    source = THEME_JS.read_text(encoding="utf-8")
    assert "colors:" not in source, "theme.js must not carry a copy of the palette"
    assert "style.setProperty" not in source, (
        "theme.js must not write tokens inline — that overrides themes.css"
    )


def test_theme_js_still_applies_the_class_and_persists() -> None:
    source = THEME_JS.read_text(encoding="utf-8")
    assert "theme-${themeName}" in source, "the theme class is how CSS themes are applied"
    assert "localStorage.setItem('selectedTheme'" in source


def test_css_defines_every_theme() -> None:
    css = THEMES_CSS.read_text(encoding="utf-8")
    for selector in (":root {", "html.theme-light {", "html.theme-lightColored {"):
        assert selector in css, f"missing theme block: {selector}"


def test_css_defines_the_tokens_the_reviewers_measured() -> None:
    """--text-muted and --focus-ring are the two tokens whose values matter for a11y."""
    css = THEMES_CSS.read_text(encoding="utf-8")
    dark = css[css.index(":root {") : css.index("html.theme-light")]
    assert "--text-muted" in dark and "--focus-ring" in dark
    light = css[css.index("html.theme-light {") : css.index("html.theme-lightColored")]
    assert "--text-muted" in light and "--focus-ring" in light
    # The dark muted token must clear AA on --card-bg (#1f1f1f).
    value = re.search(r"--text-muted:\s*(#[0-9a-fA-F]{6})", dark)
    assert value, "dark --text-muted must be a hex colour"
    r, g, b = (int(value.group(1)[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    card = 0.2126 * channel(0x1F / 255) + 0.7152 * channel(0x1F / 255) + 0.0722 * channel(0x1F / 255)
    ratio = (max(luminance, card) + 0.05) / (min(luminance, card) + 0.05)
    assert ratio >= 4.5, f"dark --text-muted contrast is {ratio:.2f}:1, below AA"
