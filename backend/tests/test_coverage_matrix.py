"""The coverage and rules matrix stays true (project-audit item 8).

The matrix is generated, so the only failure mode worth testing is drift: the
document saying one thing while the rule data, the catalogue or the calculator
registry says another. These tests regenerate it and compare.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from app.rules.loader import (
    flatten_rules,
    load_report_template,
    load_ruleset,
    load_test_catalogue,
)
from app.services.calculation_engine.calculators import supported_test_codes
from scripts.build_coverage_matrix import MATRIX_PATH, build_matrix

from tests.test_rule_boundaries import BOUNDARY_CASES


@pytest.fixture(scope="module")
def matrix() -> str:
    return build_matrix(boundary_cases=BOUNDARY_CASES) + "\n"


@pytest.fixture(scope="module")
def rows(matrix) -> dict[str, list[list[str]]]:
    """Section name -> markdown table body rows, split into cells."""
    sections: dict[str, list[list[str]]] = {}
    current: str | None = None
    header_pending = False
    for line in matrix.split("\n"):
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            header_pending = True
        elif current and line.startswith("|") and not line.startswith("| ---") and line != "| | |":
            if header_pending:
                header_pending = False
                continue
            sections[current].append([cell.strip() for cell in line.strip("|").split(" |")])
    return sections


def rule_ids(cell: str) -> list[str]:
    """The identifiers a rule cell declares (registry code, then engine id)."""
    return re.findall(r"`([^`]+)`", cell)


def test_the_committed_matrix_matches_the_generator(matrix):
    committed = io.open(MATRIX_PATH, encoding="utf-8", newline="").read().replace("\r\n", "\n")

    assert committed == matrix, (
        "docs/architecture/oiml-coverage-matrix.md is out of date; run"
        " `python -m scripts.build_coverage_matrix` from backend/"
    )


def test_every_catalogue_test_has_exactly_one_matrix_row(rows):
    catalogue = load_test_catalogue()
    codes = [entry["test_code"] for entry in catalogue["tests"] + catalogue["phase2_tests"]]
    listed = [rule_ids(row[0])[0] for row in rows["1. Test coverage"]]

    assert sorted(listed) == sorted(codes), "every catalogue entry, and nothing else"


def test_every_implemented_test_names_its_calculator(rows):
    by_code = {rule_ids(row[0])[0]: row for row in rows["1. Test coverage"]}

    for code in supported_test_codes():
        formula = by_code[code][6]
        assert "calculators.py::" in formula, f"{code} does not name the code that computes it"
        assert "no calculator registered" not in formula


def test_unimplemented_tests_stay_visible_with_their_reason(rows):
    by_code = {rule_ids(row[0])[0]: row for row in rows["1. Test coverage"]}
    catalogue = load_test_catalogue()

    for entry in catalogue["phase2_tests"]:
        row = by_code[entry["test_code"]]
        assert row[10].startswith("not_implemented"), row[10]
        assert "no calculator registered" in row[6], "an unimplemented test must not claim a formula"


def test_every_rule_in_the_ruleset_has_a_mapping(rows):
    """No active rule lacks a row, and no row is left without an explanation."""
    ruleset = load_ruleset()
    table = rows["2. Rule coverage"]
    listed = {identifier for row in table for identifier in rule_ids(row[0])}

    expected = {rule["code"] for rule in flatten_rules(ruleset)}
    missing = expected - listed
    assert not missing, f"rules with no matrix row: {sorted(missing)}"

    for row in table:
        used_by = row[7]
        assert used_by and used_by != "-", (
            f"rule {row[0]} is not used by any test and does not explain why it exists"
        )


def test_every_mpe_band_in_the_ruleset_is_listed(rows):
    ruleset = load_ruleset()
    listed = rows["3. MPE bands"]
    expected = 0
    for key in ("bands", "in_service_bands"):
        for bands in (ruleset["mpe"].get(key) or {}).values():
            expected += len(bands)

    assert len(listed) >= expected, "a resolvable MPE band is missing from the matrix"
    for row in listed:
        m_range = row[3]
        assert m_range.startswith("m = ") or " < m <= " in m_range


def test_every_report_section_has_a_provenance_row(rows):
    template = load_report_template()
    listed = {rule_ids(row[0])[0]: row for row in rows["4. Report section provenance"]}

    for section in template["sections"]:
        row = listed[section["key"]]
        assert row[4] != "-", f"section {section['key']} does not say what populates it"
        assert row[5] != "-", f"section {section['key']} does not say where the values come from"


def test_no_report_field_is_filled_by_an_unexplained_source(rows):
    """Every test block in the report is produced by a catalogue mapping."""
    template = load_report_template()
    sections = {section["key"] for section in template["sections"]}
    catalogue = load_test_catalogue()

    for entry in catalogue["tests"] + catalogue["phase2_tests"]:
        mapping = entry["report_section_mapping"]
        assert mapping["section"] in sections
        assert mapping.get("title") and mapping.get("number")

    # The narrative sections have no catalogue entry; they must still be traced.
    narrative = sections - {
        entry["report_section_mapping"]["section"] for entry in catalogue["tests"]
    }
    provenance = {rule_ids(row[0])[0] for row in rows["4. Report section provenance"]}
    assert narrative <= provenance
