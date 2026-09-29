"""A deterministic case for the golden report fixtures (project-audit item 9).

The fixture is deliberately a multi-range, repeated-observation case with one
failing test: those are exactly the shapes where a clause-to-report mapping
mistake (wrong clause text, wrong unit, wrong rounding, wrong summary row) shows
up as a plausible-looking report.

Everything the fixture contains is fixed - title, serial number, load points,
indications - so the same snapshot is produced on every run. Only identifiers
and timestamps vary, and those are normalised before comparison.
"""

from __future__ import annotations

import re
from typing import Any

API = "/api/v1"

GOLDEN_TITLE = "Golden fixture - multi-range class III bench evaluation"

GOLDEN_INSTRUMENT: dict[str, Any] = {
    "model": "GOLDEN-BENCH-30K",
    "type_designation": "GB-30K-III",
    "serial_number": "GOLDEN-MR-0001",
    "instrument_class": "III",
    "max_capacity": "30000",
    "min_capacity": "200",
    "verification_scale_interval": "10",
    "actual_scale_interval": "10",
    "unit": "g",
    "is_electronic": True,
    "is_multi_range": True,
    "has_tare_device": True,
    "has_zero_device": True,
    "has_level_indicator": True,
    "temperature_range": "-10 C to +40 C",
    "power_supply": "230 V AC, 50 Hz",
    "ranges": [
        {
            "range_no": 1,
            "min_capacity": "200",
            "max_capacity": "15000",
            "verification_scale_interval": "10",
            "actual_scale_interval": "10",
            "unit": "g",
        },
        {
            "range_no": 2,
            "min_capacity": "15000",
            "max_capacity": "30000",
            "verification_scale_interval": "20",
            "actual_scale_interval": "20",
            "unit": "g",
        },
    ],
}

#: Weighing performance across both ranges. The range-2 row fails: its corrected
#: error is 40 g (raw E = 45 g, zero-corrected by the 5 g zero reading) against
#: the 20 g MPE that applies when the range-2 interval (e = 20 g) is used. Judged
#: with the instrument-level interval instead, the report would state a 15 g
#: limit for that row, which is exactly the defect this row guards against.
GOLDEN_T_WP_ROWS = [
    {
        "observation_no": 1,
        "position_label": "Zero",
        "range_no": 1,
        "load": "0",
        "indication": "0",
        "additional_load": "0",
    },
    {
        "observation_no": 2,
        "position_label": "10 kg (range 1)",
        "range_no": 1,
        "load": "10000",
        "indication": "10000",
        "additional_load": "0",
    },
    {
        "observation_no": 3,
        "position_label": "30 kg (range 2)",
        "range_no": 2,
        "load": "30000",
        "indication": "30040",
        "additional_load": "0",
    },
]

GOLDEN_CONDITIONS = [
    {
        "label": "Ambient",
        "temperature_c": "21.5",
        "relative_humidity_pct": "48",
        "barometric_pressure_kpa": "100.8",
        "notes": "Laboratory ambient conditions recorded during the evaluation.",
    }
]

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
CODE_RE = re.compile(r"\b[0-9A-F]{12}\b")
APPLICATION_RE = re.compile(r"NAWI-\d{4}-\d{6}")
REPORT_NO_RE = re.compile(r"RPT-\d{4}-\d{5}")


def scrub_text(text: str) -> str:
    """Replace identifiers and timestamps so two runs compare equal."""
    text = APPLICATION_RE.sub("<application-no>", text)
    text = REPORT_NO_RE.sub("<report-no>", text)
    text = UUID_RE.sub("<id>", text)
    text = ISO_RE.sub("<timestamp>", text)
    text = HASH_RE.sub("<hash>", text)
    text = CODE_RE.sub("<verification-code>", text)
    text = DATE_RE.sub("<date>", text)
    return text


def scrub(value: Any) -> Any:
    """Recursively apply :func:`scrub_text` to every string in a structure."""
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def build_golden_case(client, tokens, db, observations: dict[str, list[dict]]) -> str:
    """Create the golden case through the public API and return its id.

    The observation sets come from the shared conftest fixture so the fixture
    stays valid as calculators evolve; only the weighing-performance rows are
    overridden, because the golden case must exercise both ranges.
    """
    payload: dict[str, Any] = {
        "title": GOLDEN_TITLE,
        "purpose": "Golden report fixture: clause-to-report mapping check",
        "instrument": GOLDEN_INSTRUMENT,
    }
    applicant = client.get(f"{API}/masters/applicants?page_size=1", headers=tokens["ENGINEER"])
    if applicant.status_code == 200:
        items = applicant.json().get("items") or []
        if items:
            payload["applicant_id"] = items[0]["id"]
    manufacturer = client.get(f"{API}/masters/manufacturers?page_size=1", headers=tokens["ENGINEER"])
    if manufacturer.status_code == 200:
        items = manufacturer.json().get("items") or []
        if items:
            payload["manufacturer_id"] = items[0]["id"]

    created = client.post(f"{API}/cases", json=payload, headers=tokens["ENGINEER"])
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]

    for condition in GOLDEN_CONDITIONS:
        response = client.post(
            f"{API}/cases/{case_id}/conditions", json=condition, headers=tokens["ENGINEER"]
        )
        assert response.status_code in {200, 201}, response.text

    detail = client.get(f"{API}/cases/{case_id}", headers=tokens["ENGINEER"]).json()
    for test in detail["tests"]:
        if test["applicability_status"] != "APPLICABLE":
            continue
        code = test["test_code"]
        rows = GOLDEN_T_WP_ROWS if code == "T-WP" else observations.get(code)
        assert rows is not None, f"no observation set for {code}"
        response = client.put(
            f"{API}/tests/{test['id']}/observations",
            json={"observations": rows, "replace": True},
            headers=tokens["ENGINEER"],
        )
        assert response.status_code == 200, response.text
        calculated = client.post(
            f"{API}/tests/{test['id']}/calculate", headers=tokens["ENGINEER"]
        )
        assert calculated.status_code == 200, calculated.text
        assert calculated.json()["is_valid"], calculated.json()["errors"]
        completed = client.patch(
            f"{API}/tests/{test['id']}",
            json={"mark_complete": True},
            headers=tokens["ENGINEER"],
        )
        assert completed.status_code == 200, completed.text

    return case_id
