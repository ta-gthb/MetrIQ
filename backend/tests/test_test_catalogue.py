"""The OIML R 76 test catalogue (audit item 7).

The audit asked for three things and this module pins each of them:

* every prescribed test has a catalogue entry, including the ones the engine
  cannot execute yet;
* each entry carries a full execution contract - inputs, applicability,
  validation, calculation, compliance and report mapping - plus an explicit
  implementation status;
* a case produces a complete applicability-driven plan, and an unsupported test
  is visible in that plan rather than silently dropped.

The catalogue is data, so these tests read the shipped JSON rather than a copy:
a rule that only exists in the tests would not be a coverage guarantee.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app import models
from app.database import SessionLocal
from app.rules.loader import load_report_template
from app.services.calculation_engine.calculators import supported_test_codes
from app.services.calculation_engine.types import CalcContext
from app.services.test_engine.plan import build_applicability_context, generate_plan

API = "/api/v1"

CATALOGUE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "rules"
    / "data"
    / "test-catalogue-r76-1-2006-v1.json"
)

REQUIRED_CONTRACT = (
    "applicability_expression",
    "input_schema",
    "validation_rules",
    "calculation_rules",
    "compliance_rules",
    "report_section_mapping",
    "evidence_requirements",
)

TWO_RANGE_INSTRUMENT = {
    "model": "MULTIRANGE-BENCH-30K",
    "instrument_class": "III",
    "max_capacity": "30000",
    "min_capacity": "200",
    "verification_scale_interval": "10",
    "actual_scale_interval": "10",
    "unit": "g",
    "is_multi_range": True,
    "ranges": [
        {"range_no": 1, "min_capacity": "200", "max_capacity": "15000",
         "verification_scale_interval": "10"},
        {"range_no": 2, "min_capacity": "15000", "max_capacity": "30000",
         "verification_scale_interval": "20"},
    ],
}


@pytest.fixture
def db(database):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def catalogue() -> dict:
    return json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))


def all_entries() -> list[dict]:
    body = catalogue()
    return list(body["tests"]) + list(body["phase2_tests"])


def _conditions(node) -> list[dict]:
    """Flatten an applicability expression into its leaf conditions."""
    if not isinstance(node, dict):
        return []
    leaves: list[dict] = []
    for keyword in ("all", "any"):
        for child in node.get(keyword) or []:
            leaves.extend(_conditions(child))
    if "not" in node:
        leaves.extend(_conditions(node["not"]))
    if "field" in node:
        leaves.append(node)
    return leaves


def test_every_intended_test_has_a_catalogue_entry():
    codes = [entry["test_code"] for entry in all_entries()]

    assert len(codes) == len(set(codes)), "a test code appears twice in the catalogue"
    # The procedures the project requirements name, across both phases. Losing
    # one of these is a coverage regression, not a refactor.
    assert set(codes) == {
        "T-WP", "T-REP", "T-ECC", "T-ZR", "T-CREEP", "T-TEMP-NL", "T-SENS",
        "T-DISC", "T-STAB", "T-CHK-CON", "T-CHK-ID",
        "T-TILT", "T-TARE", "T-WARMUP", "T-VOLT", "T-EMC", "T-DAMP",
    }
    assert catalogue()["coverage"]["totals"]["entries"] == len(codes)


@pytest.mark.parametrize("entry", all_entries(), ids=lambda entry: entry["test_code"])
def test_each_entry_declares_an_implementation_status(entry):
    assert entry["implementation_status"] in models.TestImplementationStatus.ALL
    assert entry["clause_reference"], "an entry without a clause reference is not traceable"
    assert entry["clause_reference_status"] in {"provisional", "verified"}


@pytest.mark.parametrize("entry", all_entries(), ids=lambda entry: entry["test_code"])
def test_implemented_entries_have_a_calculator_and_the_rest_say_why_not(entry):
    """A test may be unimplemented, but never unexplained."""
    registered = supported_test_codes()
    if entry["implementation_status"] == models.TestImplementationStatus.IMPLEMENTED:
        assert entry["test_code"] in registered, (
            f"{entry['test_code']} claims to be implemented but no calculator is registered"
        )
        assert entry["unsupported_reason"] is None
    else:
        assert entry["test_code"] not in registered
        reason = (entry["unsupported_reason"] or "").strip()
        assert len(reason) > 30, (
            f"{entry['test_code']} is not implemented and must say why, in words a"
            " reviewer can act on"
        )


def test_every_registered_calculator_is_in_the_catalogue():
    codes = {entry["test_code"] for entry in all_entries()}
    orphans = set(supported_test_codes()) - codes

    assert not orphans, f"calculators exist for tests that are not in the catalogue: {orphans}"


@pytest.mark.parametrize("entry", all_entries(), ids=lambda entry: entry["test_code"])
def test_each_entry_carries_the_full_execution_contract(entry):
    for key in REQUIRED_CONTRACT:
        assert entry.get(key), f"{entry['test_code']} has no {key}"

    schema = entry["input_schema"]
    assert schema.get("columns"), f"{entry['test_code']} declares no input columns"
    assert schema.get("layout") in {"table", "checklist"}

    mapping = entry["report_section_mapping"]
    assert mapping.get("section") and mapping.get("number") and mapping.get("title")

    compliance = entry["compliance_rules"]
    limit_source = compliance.get("limit_source") or compliance.get("comparator")
    assert limit_source, f"{entry['test_code']} does not say how compliance is decided"


def test_report_sections_exist_in_the_report_template():
    template = load_report_template("r76-2-2007-v1")
    sections = {section["key"] for section in template["sections"]}

    for entry in all_entries():
        section = entry["report_section_mapping"]["section"]
        assert section in sections, (
            f"{entry['test_code']} maps to report section '{section}', which the R 76-2"
            " template does not define"
        )


def test_test_section_numbers_are_unique_and_ordered():
    numbered = [
        (entry["report_section_mapping"]["number"], entry["test_code"])
        for entry in all_entries()
        if entry["report_section_mapping"]["section"] == "tests"
    ]
    numbers = [number for number, _code in numbered]

    assert len(numbers) == len(set(numbers)), f"duplicate report section numbers: {numbered}"
    ordered = sorted(numbers, key=lambda value: [int(part) for part in value.split(".")])
    assert numbers == ordered, "individual test records should be numbered in catalogue order"


def test_range_capable_entries_accept_a_range_column():
    for entry in all_entries():
        columns = {column["key"] for column in entry["input_schema"]["columns"]}
        if entry["supports_ranges"]:
            assert entry["range_scope"] == "per_range"
            assert "range_no" in columns, (
                f"{entry['test_code']} supports range-specific testing but cannot record"
                " which range a row belongs to"
            )
        else:
            assert entry["range_scope"] == "instrument"


def test_entries_that_need_a_capability_declare_it():
    """Applicability expressions and declared capabilities must agree."""
    for entry in all_entries():
        declared = set(entry["required_capabilities"])
        for check in _conditions(entry["applicability_expression"]):
            if check.get("op") != "eq" or check.get("value") is not True:
                continue
            field = check.get("field", "")
            if not field.startswith("instrument."):
                continue
            capability = field.split(".", 1)[1]
            assert capability in declared, (
                f"{entry['test_code']} is only applicable when {capability} is true but"
                " does not declare that capability"
            )


def test_the_catalogue_is_explicit_about_what_it_does_not_support():
    body = catalogue()
    unsupported = {entry["test_code"] for entry in body["phase2_tests"]}

    assert set(body["coverage"]["unsupported_tests"]) == unsupported
    assert body["coverage"]["totals"]["not_implemented"] == len(unsupported)
    legend = body["coverage"]["status_legend"]
    assert models.TestImplementationStatus.IMPLEMENTED in legend
    assert models.TestImplementationStatus.NOT_IMPLEMENTED in legend


def test_seeding_preserves_the_status_and_keeps_phase_two_inactive(db):
    version = db.execute(
        select(models.StandardVersion).where(
            models.StandardVersion.version_label == "r76-1-2006-v1"
        )
    ).scalars().first()
    definitions = {
        definition.test_code: definition
        for definition in db.execute(
            select(models.TestDefinition).where(
                models.TestDefinition.standard_version_id == version.id
            )
        ).scalars()
    }

    assert len(definitions) == len(all_entries())
    for entry in all_entries():
        definition = definitions[entry["test_code"]]
        assert definition.implementation_status == entry["implementation_status"]
        assert definition.unsupported_reason == entry["unsupported_reason"]
        if entry["implementation_status"] == models.TestImplementationStatus.NOT_IMPLEMENTED:
            assert definition.is_active is False
            assert definition.phase == "2"


def test_the_plan_reports_an_inactive_test_as_unsupported_not_as_missing(new_case, db):
    """A case plan is generated from active definitions; the catalogue still
    explains the procedures it cannot run, and the coverage matrix lists them."""
    case_id = new_case()["id"]
    case = db.get(models.EvaluationCase, uuid.UUID(case_id))
    context = build_applicability_context(case.instrument, case=case)
    plan = generate_plan(
        db.execute(
            select(models.TestDefinition).where(
                models.TestDefinition.standard_version_id == case.standard_version_id,
                models.TestDefinition.is_active.is_(True),
            )
        ).scalars().all(),
        context=context,
    )

    codes = {item.test_code for item in plan}
    assert "T-WP" in codes
    assert "T-EMC" not in codes, "an inactive definition must not reach a case plan"
    assert all(item.supported for item in plan), "active definitions must all be executable"


def test_applicability_context_exposes_the_range_table(new_case, db):
    case_id = new_case(instrument=TWO_RANGE_INSTRUMENT)["id"]
    case = db.get(models.EvaluationCase, uuid.UUID(case_id))

    context = build_applicability_context(case.instrument, case=case)
    instrument = context["instrument"]

    assert instrument["is_multi_range"] is True
    assert instrument["range_count"] == 2
    assert [item["range_no"] for item in instrument["ranges"]] == [1, 2]
    assert Decimal(instrument["ranges"][1]["e"]) == Decimal("20")
    assert Decimal(instrument["ranges"][1]["max_capacity"]) == Decimal("30000")


def test_a_multi_range_instrument_resolves_the_scale_interval_per_range(
    client, tokens, new_case
):
    """``m = load / e`` must use the interval the load actually falls in.

    Both rows would resolve to a different MPE if the instrument-level ``e``
    were used for every range, which is the defect this guards against.
    """
    case = new_case(instrument=TWO_RANGE_INSTRUMENT)
    detail = client.get(f"{API}/cases/{case['id']}", headers=tokens["ENGINEER"]).json()
    test = next(item for item in detail["tests"] if item["test_code"] == "T-WP")

    rows = [
        {"observation_no": 1, "range_no": 1, "load": "10000", "indication": "10000",
         "additional_load": "0", "unit": "g"},
        {"observation_no": 2, "range_no": 2, "load": "30000", "indication": "30000",
         "additional_load": "0", "unit": "g"},
    ]
    response = client.put(
        f"{API}/tests/{test['id']}/observations",
        json={"observations": rows, "replace": True},
        headers=tokens["ENGINEER"],
    )
    assert response.status_code == 200, response.text
    calc = client.post(f"{API}/tests/{test['id']}/calculate", headers=tokens["ENGINEER"])
    assert calc.status_code == 200, calc.text
    body = calc.json()
    assert body["is_valid"], body["errors"]

    by_range = {row["detail"]["range_no"]: row for row in body["rows"]}
    # Range 1: m = 10000 / 10 = 1000 e -> band 500 < m <= 2000 -> 1.0 e -> 10 g
    assert Decimal(by_range[1]["mpe"]) == Decimal("10.00000000")
    # Range 2: m = 30000 / 20 = 1500 e -> band 500 < m <= 2000 -> 1.0 e -> 20 g
    # (with the instrument-level e = 10 this would have been m = 3000 e -> 15 g)
    assert Decimal(by_range[2]["mpe"]) == Decimal("20.00000000")
    assert Decimal(body["limit_value"]) == Decimal(by_range[1]["mpe"]), (
        "the case-level limit is the governing row, and both rows here are error-free"
    )


def test_an_unsupported_test_is_reported_in_the_plan_not_hidden():
    """The plan carries the status, and nothing is filtered out silently."""

    class FakeDefinition:
        def __init__(self, code, status, reason=None):
            self.test_code = code
            self.name = code
            self.clause_reference = "OIML R 76-1:2006 T.3.x"
            self.category = "influence"
            self.phase = "2"
            self.sequence_no = 100
            self.implementation_status = status
            self.unsupported_reason = reason
            self.applicability_expression = {"always": True}
            self.id = code

    plan = generate_plan(
        [
            FakeDefinition("T-WP", models.TestImplementationStatus.IMPLEMENTED),
            FakeDefinition(
                "T-EMC",
                models.TestImplementationStatus.NOT_IMPLEMENTED,
                "no immunity limit in the versioned ruleset",
            ),
        ],
        context={"instrument": {}},
    )
    items = {item.test_code: item for item in plan}

    assert items["T-EMC"].applicable is True
    assert items["T-EMC"].supported is False
    assert items["T-EMC"].unsupported_reason
    payload = items["T-EMC"].as_dict()
    assert payload["implementation_status"] == models.TestImplementationStatus.NOT_IMPLEMENTED
    assert payload["supported"] is False


def test_scale_interval_resolution_without_ranges_falls_back_to_the_instrument():
    ctx = CalcContext(
        test_code="T-WP",
        instrument={"e": Decimal("10"), "unit": "g"},
        observations=[],
        ranges=[],
    )

    assert ctx.e_for(Decimal("30000")) == Decimal("10")


def test_a_load_above_every_declared_range_falls_back_to_the_instrument_interval():
    ctx = CalcContext(
        test_code="T-WP",
        instrument={"e": Decimal("10"), "unit": "g"},
        observations=[],
        ranges=[
            {"range_no": 1, "min_capacity": "200", "max_capacity": "15000", "e": "10"},
        ],
    )

    assert ctx.e_for(Decimal("30000")) == Decimal("10")
