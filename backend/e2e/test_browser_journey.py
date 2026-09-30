"""Browser end-to-end journey (audit item 18).

These tests drive the real application in Chromium: the public home page, the
sign-in journey and the evaluation workspace, including the advisory
report-consistency review. They prove the deployed wiring (pages, API, session,
theme, clock) works together, not just that each unit works alone.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from e2e.conftest import ADMIN_ACCOUNT, DEFAULT_PASSWORD


def test_the_home_page_plays_the_startup_animation_and_live_statistics(page, server):
    page.goto(f"{server}/")
    expect(page).to_have_title(re.compile("MetrIQ"))
    # The startup animation is an overlay: it must be present and then clear.
    expect(page.locator(".splash-logo")).to_be_visible()
    expect(page.locator(".splash")).to_have_count(0, timeout=8000)

    grid = page.locator("[data-statistics]")
    expect(grid).to_have_attribute("data-rendered", "true")
    expect(grid.locator("article.stat")).to_have_count(8)
    expect(grid.locator(".stat-value").first).not_to_have_text("\u2014")

    clock = page.locator("[data-clock] [data-clock-time]").first
    expect(clock).not_to_have_text("--:--:--")


def test_the_theme_switch_persists_across_pages(page, server):
    page.goto(f"{server}/")
    toggle = page.locator("#theme-toggle")
    expect(toggle).to_be_visible()
    original = page.evaluate("document.documentElement.getAttribute('data-theme')")
    toggle.click()
    switched = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert switched != original
    assert page.evaluate("localStorage.getItem('metriq-theme')") == switched

    page.goto(f"{server}/login.html")
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == switched


def test_sign_in_opens_the_workspace_and_runs_the_consistency_review(page, server):
    page.goto(f"{server}/login.html")
    page.fill("#user-id", ADMIN_ACCOUNT)
    page.fill("#password", DEFAULT_PASSWORD)
    page.click("#submit")
    page.wait_for_url(re.compile(r"/dashboard\.html"))
    expect(page.locator("#shell")).to_contain_text("MetrIQ")

    page.goto(f"{server}/evaluations.html")
    first_case = page.locator('a[href^="/evaluation.html?case="]').first
    expect(first_case).to_be_visible()
    case_id = first_case.get_attribute("href").split("case=", 1)[1].split("&", 1)[0]

    page.goto(f"{server}/evaluation.html?case={case_id}&step=review")
    card = page.locator("div.card", has_text="Report-consistency review").last
    expect(card).to_be_visible()
    card.locator("#btn-consistency").click()
    expect(card.locator("table, .banner.pass, .banner.warn").first).to_be_visible(timeout=40000)
