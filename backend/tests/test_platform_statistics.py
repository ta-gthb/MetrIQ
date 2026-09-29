"""The public home page's statistics contract.

``/api/v1/platform/statistics`` is the only endpoint on the platform that
answers without a token, so most of what follows is about what it must *not*
disclose. The page has to be able to say how much work this instance has handled
without saying whose work it was.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from app.services import platform_statistics

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
API = "/api/v1"
STATISTICS = f"{API}/platform/statistics"

#: A UUID in any record-reference position would mean a count had leaked a row.
UUID_LIKE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

#: Leaf values a public payload may carry, besides numbers and booleans.
PUBLISHED_STRINGS = re.compile(
    r"^(OIML R 76-1:2006|OIML R 76-2:2007|draft|active|scheduled|superseded|none|"
    r"provisional|reviewed|unmanaged|[A-Za-z0-9._-]{1,40})$"
)

#: ISO-8601 UTC, the only free-form-looking value the payload legitimately holds.
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")


@pytest.fixture(autouse=True)
def fresh_cache():
    """No test inherits another's cached payload."""
    platform_statistics.reset_cache()
    yield
    platform_statistics.reset_cache()


def walk(node, path=""):
    """Yield ``(path, value)`` for every leaf in the payload."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")
    else:
        yield path, node


def test_the_endpoint_answers_without_a_token(client):
    response = client.get(STATISTICS)

    assert response.status_code == 200, response.text
    assert response.json()["generated_at"]


def test_it_publishes_the_aggregates_the_home_page_shows(client):
    body = client.get(STATISTICS).json()

    generated = datetime.fromisoformat(body["generated_at"])
    assert generated.tzinfo is not None, "the page reports the moment its figures were read"
    assert body["refresh_seconds"] > 0

    standards = body["standards"]
    assert standards["framework"] == ["OIML R 76-1:2006", "OIML R 76-2:2007"]
    assert standards["test_definitions"] > 0, "the shipped catalogue is always present"
    assert standards["ruleset"]["state"]

    for section in ("evaluations", "testing", "reports", "network", "activity"):
        assert body[section], section
        assert all(isinstance(value, int) for value in body[section].values()), section


def test_it_is_deliberately_aggregate(client):
    """Counts leave the building; records do not.

    Every leaf has to be a number, a boolean, or one of the few configuration
    facts the platform already publishes - no identifier, no free text, and no
    value that could be pasted back into another endpoint.
    """
    body = client.get(STATISTICS).json()

    for path, value in walk(body):
        if value is None or isinstance(value, (bool, int)):
            # `activation_basis` and `version_label` are null only on an
            # instance whose ruleset was never seeded.
            continue
        assert isinstance(value, str), path
        assert not UUID_LIKE.match(value), f"{path} looks like a row reference"
        if TIMESTAMP.match(value):
            continue
        assert PUBLISHED_STRINGS.match(value), f"{path} publishes free text: {value!r}"

    keys = {path.rsplit(".", 1)[-1].lower() for path, _ in walk(body)}
    for forbidden in ("id", "uuid", "email", "name", "title", "serial", "password", "token"):
        assert forbidden not in keys, forbidden


def test_the_figures_come_from_the_database_not_the_build(client, new_case):
    before = client.get(STATISTICS).json()

    new_case()

    platform_statistics.reset_cache()
    after = client.get(STATISTICS).json()

    assert after["evaluations"]["total"] == before["evaluations"]["total"] + 1
    assert after["evaluations"]["open"] == before["evaluations"]["open"] + 1
    assert after["network"]["instruments"] == before["network"]["instruments"] + 1
    assert after["generated_at"] >= before["generated_at"]


def test_a_repeat_within_the_window_is_served_from_cache(client, new_case):
    """One set of queries per window, however many visitors arrive in it."""
    first = client.get(STATISTICS).json()
    new_case()

    cached = client.get(STATISTICS).json()
    assert cached["generated_at"] == first["generated_at"]
    assert cached["evaluations"]["total"] == first["evaluations"]["total"]

    platform_statistics.reset_cache()
    refreshed = client.get(STATISTICS).json()
    assert refreshed["generated_at"] != first["generated_at"]
    assert refreshed["evaluations"]["total"] == first["evaluations"]["total"] + 1


def test_the_window_stays_short_enough_for_the_page_to_be_live():
    assert 0 < platform_statistics.CACHE_TTL_SECONDS <= platform_statistics.REFRESH_SECONDS
    assert platform_statistics.REFRESH_SECONDS <= 60, "a slower poll is not real time"


def test_the_api_index_points_at_the_statistics(client):
    body = client.get(API).json()

    assert body["statistics"] == STATISTICS


# ------------------------------------------------------------- the home page ---


def test_the_home_page_carries_an_about_a_features_and_a_statistics_section():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")

    for anchor in ('id="about"', 'id="features"', 'id="statistics"', "data-statistics"):
        assert anchor in html, anchor
    assert "/js/home.js" in html
    # The switch and the clock are part of every page (see test_frontend_theme).
    assert "data-theme-toggle" in html
    assert "data-clock" in html


def test_the_home_page_refreshes_itself_without_reloading():
    source = (FRONTEND / "js" / "home.js").read_text(encoding="utf-8")

    assert "/platform/statistics" in source
    assert "setTimeout" in source, "the figures have to refresh themselves"
    assert "visibilitychange" in source, "a backgrounded page must not keep polling"
    assert "escapeHtml" in source, "a value from the API is written into the page"
