"""The metrology review register (audit item 6 / checklist item 9).

OIML requirements, formulas, limits and clause references must be confirmed by a
competent metrology reviewer before the platform decides a real verification.
Code cannot perform that review, but it can refuse to let the register drift away
from the rules the platform actually applies: every rule in the versioned
ruleset must appear in the register, a rule may only be marked confirmed when a
reviewer and a date are recorded, and a limit that is still a proposal must say
so.
"""

from __future__ import annotations

import io
from pathlib import Path

from app.rules.loader import flatten_rules, load_ruleset

REGISTER = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "governance"
    / "metrology-review-register.md"
)

STATUSES = {"pending", "confirmed", "replaced"}


def register_text() -> str:
    assert REGISTER.exists(), "the metrology review register is missing"
    return io.open(REGISTER, encoding="utf-8", newline="").read().replace("\r\n", "\n")


def register_rows() -> dict[str, list[str]]:
    """The rule rows of the register, keyed by rule code."""
    rows: dict[str, list[str]] = {}
    for line in register_text().split("\n"):
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = cells
    return rows


def test_the_register_covers_every_rule_in_the_ruleset():
    expected = {rule["code"] for rule in flatten_rules(load_ruleset())}
    listed = set(register_rows())

    assert expected <= listed, f"rules with no register row: {sorted(expected - listed)}"
    assert not (listed - expected), f"register rows for rules that do not exist: {sorted(listed - expected)}"


def test_the_register_asks_for_every_part_of_the_review():
    text = register_text()
    for phrase in (
        "Formulas",
        "MPE bands",
        "Tolerances",
        "Applicability",
        "Observation structure",
        "Unit conversion",
        "Rounding",
        "test_rule_boundaries.py",
    ):
        assert phrase in text, f"the register does not cover '{phrase}'"


def test_a_rule_can_only_be_confirmed_by_a_named_reviewer_on_a_date():
    for code, cells in register_rows().items():
        status = cells[4].lower()
        assert status in STATUSES, f"{code} has an unknown review status '{cells[4]}'"
        reviewer, reviewed_on = cells[5], cells[6]
        if status == "pending":
            continue
        assert reviewer not in {"", "-"}, f"{code} is marked {status} without a reviewer"
        assert reviewed_on not in {"", "-"}, f"{code} is marked {status} without a date"


def test_a_limit_that_is_still_a_proposal_is_marked_pending():
    ruleset = load_ruleset()
    for rule in flatten_rules(ruleset):
        if rule["category"] != "tolerance":
            continue
        definition = rule.get("definition") or {}
        cells = register_rows()[rule["code"]]
        if definition.get("review_status") == "pending_domain_review":
            assert cells[3] == "proposal", f"{rule['code']} does not say its value is a proposal"
            assert cells[4] == "pending", f"{rule['code']} is not marked pending review"
            assert "REVIEW REQUIRED" in cells[1], f"{rule['code']} does not flag its clause reference"
        else:
            assert cells[3] in {"project baseline", "reviewed"}, rule["code"]


def test_the_sign_off_block_is_present_and_complete_enough_to_fill_in():
    text = register_text()
    assert "## 3. Sign-off" in text
    for field in ("Reviewer (name, qualification)", "Laboratory / authority", "Decision", "Date"):
        assert field in text, f"the sign-off block has no '{field}' field"
