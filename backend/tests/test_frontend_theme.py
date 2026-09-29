"""Contracts for the light/dark switch and the always-current clock.

There is no JavaScript test harness in this repository, so these tests guard
the parts that are hand-maintained and therefore easy to forget: every page
shell must carry the pre-paint theme bootstrap, the palette must be complete
in both themes, and the shared shell must ship the switch and the clock.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

APP_PAGES = ["admin.html", "assistant.html", "dashboard.html", "evaluation.html",
             "evaluations.html", "reports.html"]
AUTH_PAGES = ["index.html", "login.html"]

# Colour tokens every theme must define; anything here missing from the light
# block would silently render as the dark value.
THEMED_TOKENS = [
    "--bg", "--bg-raised", "--bg-inset", "--surface", "--surface-2",
    "--border", "--border-strong", "--text", "--text-muted", "--text-faint",
    "--accent", "--accent-strong", "--accent-ink", "--info", "--pass", "--fail",
    "--warn", "--na", "--shadow", "--bg-grad", "--sidebar-bg", "--chrome-bg",
    "--chrome-bg-strong", "--backdrop", "--danger-ink",
]


def read(relative: str) -> str:
    with io.open(FRONTEND / relative, encoding="utf-8", newline="") as handle:
        return handle.read()


def test_every_page_applies_the_stored_theme_before_first_paint():
    """The bootstrap is a file, not an inline script (audit item 13).

    It is referenced from <head> and loaded synchronously, so it still runs
    before the first paint - the property that keeps a light-mode page from
    flashing the dark palette.
    """
    boot = read("js/theme-boot.js")
    assert "metriq-theme" in boot, "the bootstrap no longer reads the stored theme"
    assert "data-theme" in boot

    for name in APP_PAGES + AUTH_PAGES:
        body = read(name)
        assert "/js/theme-boot.js" in body, f"{name} has no pre-paint theme bootstrap"
        head = body[: body.index("</head>")]
        assert "/js/theme-boot.js" in head, f"{name} must load the bootstrap from <head>"

    # The bootstrap stamps data-theme onto <html>.  The two script-bearing
    # pages also declare it statically so the very first paint is themed even
    # before JavaScript runs; the application pages get it from renderShell.
    for name in AUTH_PAGES:
        assert "data-theme" in read(name), name
    assert "data-theme" in read("js/ui.js"), "renderShell no longer themes <html>"


def test_every_page_offers_a_theme_switch():
    # Application pages get theirs from renderShell (see the shell test below).
    for name in AUTH_PAGES:
        body = read(name)
        assert "data-theme-toggle" in body, f"{name} has no theme switch"
        assert "data-clock" in body, f"{name} has no live clock"


def test_shell_renders_the_theme_switch_and_the_live_clock():
    shell = read("js/ui.js")
    assert "data-theme-toggle" in shell
    assert "data-clock" in shell
    assert "initTheme()" in shell
    assert "startClocks()" in shell


def test_theme_module_persists_the_choice_and_follows_the_os():
    module = read("js/theme.js")
    assert "'metriq-theme'" in module
    assert "prefers-color-scheme: light" in module
    assert "[data-theme-toggle]" in module


def test_clock_corrects_itself_from_the_server_clock():
    module = read("js/clock.js")
    assert "server_time_utc" in module
    assert "setInterval" in module


def test_light_theme_restates_every_colour_token():
    css = read("css/app.css")
    start = css.index('[data-theme="light"]')
    light = css[start : css.index("}", start)]
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", light))
    missing = [token for token in THEMED_TOKENS if token not in declared]
    assert not missing, f"light theme does not define: {missing}"


def test_every_css_variable_reference_is_declared():
    css = read("css/app.css")
    root = css[css.index(":root {") : css.index('[data-theme="light"]')]
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", root))
    referenced = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    assert referenced <= declared, sorted(referenced - declared)
