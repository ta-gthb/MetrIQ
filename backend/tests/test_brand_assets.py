"""Contracts for the official mark and the startup animation.

The brand mark is a commissioned raster lockup rather than a drawn glyph, so
the pages and the stylesheet have to agree on where it goes and at what ratio.
The startup animation is an overlay driven by the stylesheet, and these tests
pin the two properties that matter: it cannot leave the page covered, and it
never plays for a visitor who asked for reduced motion. Neither of those is
visible to the API tests, so they are guarded here.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

HTML_PAGES = [
    "index.html", "login.html", "reset-password.html", "dashboard.html",
    "evaluations.html", "evaluation.html", "reports.html", "admin.html",
    "assistant.html",
]

ASSETS = [
    "assets/logo.png",
    "assets/logo-emblem.png",
    "assets/favicon.ico",
    "assets/apple-touch-icon.png",
]

ASSET_REFERENCE = re.compile(r"/assets/([A-Za-z0-9._-]+)")


def read(relative: str) -> str:
    with io.open(FRONTEND / relative, encoding="utf-8", newline="") as handle:
        return handle.read()


def test_the_mark_is_on_disk_in_every_form_the_pages_need():
    for name in ASSETS:
        path = FRONTEND / name
        assert path.is_file(), f"{name} is missing"
        assert path.stat().st_size > 1000, f"{name} is empty or truncated"


def test_the_drawn_placeholder_mark_is_gone():
    assert not (FRONTEND / "assets" / "logo.svg").exists()


def test_every_page_serves_an_icon_that_exists():
    for name in HTML_PAGES:
        html = read(name)
        icons = re.findall(r'<link rel="(?:icon|apple-touch-icon)" href="([^"]+)"', html)
        assert len(icons) == 2, f"{name} must offer a favicon and a touch icon"
        for href in icons:
            assert href.startswith("/assets/"), (name, href)
            assert (FRONTEND / href.lstrip("/")).is_file(), (name, href)


def test_every_asset_the_interface_asks_for_is_shipped():
    sources = [read(name) for name in HTML_PAGES]
    for name in ("js/ui.js", "js/home.js", "js/splash-boot.js"):
        sources.append(read(name))
    for source in sources:
        for reference in ASSET_REFERENCE.findall(source):
            assert (FRONTEND / "assets" / reference).is_file(), reference


def test_the_brand_block_places_the_emblem_at_its_own_ratio():
    """A wide lockup squeezed into a square box reads as a distorted glyph."""
    for name in ("index.html", "login.html", "reset-password.html"):
        assert '<img src="/assets/logo-emblem.png" alt=""' in read(name), name
    assert "/assets/logo-emblem.png" in read("js/ui.js")
    css = read("css/app.css")
    brand = re.search(r"\.brand img \{([^}]*)\}", css)
    assert brand, "the brand mark has no rule"
    assert "width: auto" in brand.group(1)
    assert "height: 30px" in brand.group(1)


def test_the_home_page_carries_the_official_lockup():
    html = read("index.html")
    assert '<img class="hero-logo" src="/assets/logo.png" alt="MetrIQ"' in html
    assert re.search(r"\.hero-logo \{[^}]*width: min\(", read("css/app.css"))


def test_the_startup_animation_cannot_leave_the_page_covered():
    html = read("index.html")
    assert 'data-splash' in html
    assert '<img class="splash-logo" src="/assets/logo.png"' in html
    css = read("css/app.css")
    splash = re.search(r"\.splash \{([^}]*)\}", css)
    assert splash, "the startup overlay has no rule"
    assert "position: fixed" in splash.group(1)
    # The dismissal lives in the stylesheet, so it happens whether or not any
    # script runs.
    assert re.search(r"animation: metriq-splash-out[^;]*forwards", splash.group(1))
    keyframes = re.search(r"@keyframes metriq-splash-out \{(.*?)\n\}", css, re.S)
    assert keyframes and "visibility: hidden" in keyframes.group(1)
    assert re.search(r'html\[data-splash="off"\] \.splash \{ display: none; \}', css)
    assert re.search(
        r"@media \(prefers-reduced-motion: reduce\) \{ \.splash \{ display: none; \} \}", css
    )


def test_the_startup_animation_plays_once_per_session():
    html = read("index.html")
    head = html[: html.index("</head>")]
    assert '<script src="/js/splash-boot.js"></script>' in head
    boot = read("js/splash-boot.js")
    assert "sessionStorage" in boot
    assert "setAttribute('data-splash', 'off')" in boot
    # The finished overlay is taken out of the document by the page module.
    assert "querySelector('[data-splash]')" in read("js/home.js")


def test_the_switch_word_waits_until_the_theme_is_known():
    css = read("css/app.css")
    assert "html:not([data-theme-ready]) .theme-toggle .theme-icon" in css
    assert "html:not([data-theme-ready]) .theme-toggle .theme-toggle-label" in css
    assert "setAttribute('data-theme-ready', 'true')" in read("js/theme.js")