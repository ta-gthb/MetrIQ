"""Load and flatten the versioned rule data shipped with the application."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"

REPORT_TEMPLATE_FILES = {
    "r76-2-2007-v1": "r76-2-2007-v1.json",
}


def _read(filename: str) -> dict[str, Any]:
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"rule data file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _ruleset_files() -> dict[str, str]:
    files = {}
    for path in DATA_DIR.glob("*.json"):
        if path.name.startswith("test-catalogue-"):
            continue
        try:
            payload = _read(path.name)
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("version_label") and payload.get("standard") and any(
            key in payload for key in ("mpe", "tolerances", "rules")
        ):
            files[payload["version_label"]] = path.name
    return files


@lru_cache
def load_ruleset(version_label: str = "r76-1-2006-v1") -> dict[str, Any]:
    try:
        filename = _ruleset_files()[version_label]
    except KeyError as exc:
        raise FileNotFoundError(f"ruleset version not found: {version_label}") from exc
    return _read(filename)


@lru_cache
def load_test_catalogue(version_label: str = "r76-1-2006-v1") -> dict[str, Any]:
    filename = f"test-catalogue-{version_label}.json"
    path = DATA_DIR / filename
    return _read(filename) if path.exists() else {"tests": []}


@lru_cache
def load_report_template(version_label: str = "r76-2-2007-v1") -> dict[str, Any]:
    return _read(REPORT_TEMPLATE_FILES[version_label])


def flatten_rules(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Explode a ruleset payload into individually addressable rule records.

    Each returned entry maps onto a ``rules`` + ``rule_versions`` pair so a
    laboratory can inspect, diff and re-approve a single band or tolerance
    without touching the rest of the rule set (PRD 24.1, FR "Rules").
    """
    version_label = payload.get("version_label", "unversioned")
    clause = ((payload.get("mpe") or {}).get("clause_reference")) or ""
    entries: list[dict[str, Any]] = []

    for rule in payload.get("rules") or []:
        entries.append(
            {
                "code": rule["code"],
                "name": rule.get("name") or rule["code"],
                "category": rule.get("category", "rule"),
                "clause_reference": rule.get("clause_reference") or clause,
                "definition": rule.get("definition") or {},
                "formula": rule.get("formula"),
                "threshold": rule.get("threshold"),
                "unit": rule.get("unit"),
                "applicability": rule.get("applicability"),
                "rounding_policy": rule.get("rounding_policy"),
            }
        )

    mpe = payload.get("mpe") or {}
    for stage, key in (("verification", "bands"), ("in_service", "in_service_bands")):
        bands_by_class = mpe.get(key) or {}
        for instrument_class, bands in bands_by_class.items():
            code = f"R76-MPE-{instrument_class}-{stage.upper()}"
            entries.append(
                {
                    "code": code,
                    "name": f"MPE bands for class {instrument_class} ({stage.replace('_', ' ')})",
                    "category": "mpe",
                    "clause_reference": mpe.get("clause_reference") or clause,
                    "definition": {
                        "instrument_class": instrument_class,
                        "stage": stage,
                        "unit": mpe.get("unit", "e"),
                        "bands": bands,
                    },
                    "formula": "m = load / e ; mpe = factor * e (or factor, per unit)",
                    "unit": mpe.get("unit", "e"),
                    "applicability": {"instrument_class": instrument_class, "stage": stage},
                    "rounding_policy": "ROUND_HALF_UP at display resolution only",
                }
            )
    if mpe.get("at_zero"):
        entries.append(
            {
                "code": "R76-MPE-ZERO",
                "name": "MPE at zero load",
                "category": "mpe",
                "clause_reference": mpe["at_zero"].get("clause_reference"),
                "definition": mpe["at_zero"],
                "formula": "mpe_at_zero = factor * e",
                "unit": mpe["at_zero"].get("unit", "e"),
                "rounding_policy": "ROUND_HALF_UP at display resolution only",
            }
        )

    for key, tolerance in (payload.get("tolerances") or {}).items():
        entries.append(
            {
                "code": f"R76-{key.upper().replace('_', '-')}",
                "name": tolerance.get("description") or key.replace("_", " ").title(),
                "category": "tolerance",
                "clause_reference": tolerance.get("clause_reference"),
                "definition": tolerance,
                "formula": "limit = factor * e (or factor * d)",
                "threshold": tolerance.get("factor"),
                "unit": tolerance.get("unit"),
                "rounding_policy": "ROUND_HALF_UP at display resolution only",
            }
        )

    for entry in entries:
        entry["version_label"] = version_label
    return entries


def available_rulesets() -> list[str]:
    return sorted(_ruleset_files())
