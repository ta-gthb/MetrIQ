"""Compare two report snapshots revision by revision (audit item 13).

The repository keeps the snapshot each report revision printed, so a reviewer
can ask what actually changed between two revisions: which test result moved,
which reading changed, which section of the rule set or template was used.
The comparison is structural and deterministic - it reads only the snapshots -
so it can also answer for a finalized report whose records cannot be edited.
"""

from __future__ import annotations

from typing import Any, Iterator

__all__ = ["compare_snapshots", "UNCHANGED_AREAS"]

# Areas compared field by field. ``conditions``, ``evidence`` and ``tests``
# are handled separately because they are lists of records.
UNCHANGED_AREAS = ("meta", "versions", "cover", "instrument", "summary", "review")


def _revision_label(snapshot: dict) -> dict:
    meta = (snapshot or {}).get("meta") or {}
    return {
        "revision_no": meta.get("revision_no"),
        "case_revision_no": meta.get("case_revision_no"),
        "generated_at": meta.get("generated_at"),
        "verification_code": meta.get("verification_code"),
    }


def _leaf_differences(before: Any, after: Any, prefix: str = "") -> Iterator[tuple[str, Any, Any]]:
    """Yield (field, before, after) for every leaf that differs.

    Dicts recurse; lists of dicts are compared item by item (the label is the
    record's observation number when it has one, otherwise its position) so a
    changed reading is a row-level change, not one large blob.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            name = f"{prefix}.{key}" if prefix else str(key)
            yield from _leaf_differences(before.get(key), after.get(key), name)
        return
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        if all(isinstance(item, dict) for item in before + after):
            for index, (old, new) in enumerate(zip(before, after)):
                label = old.get("observation_no") or new.get("observation_no") or (index + 1)
                yield from _leaf_differences(old, new, f"{prefix}[{label}]")
            return
    if before != after:
        yield prefix, before, after


def _area_changes(area: str, before: dict, after: dict) -> list[dict]:
    changes = []
    for field, old, new in _leaf_differences(before.get(area) or {}, after.get(area) or {}):
        changes.append({"area": area, "code": None, "field": field, "before": old, "after": new})
    return changes


def _list_changes(area: str, before: list, after: list) -> list[dict]:
    changes = []
    for field, old, new in _leaf_differences(before or [], after or []):
        changes.append({"area": area, "code": None, "field": field, "before": old, "after": new})
    return changes


def _by_code(snapshot: dict) -> dict[str, dict]:
    return {
        item.get("test_code"): item
        for item in (snapshot or {}).get("tests", [])
        if item.get("test_code")
    }


def _test_label(test: dict | None) -> dict | None:
    if test is None:
        return None
    result = test.get("result") or {}
    return {
        "name": test.get("name"),
        "status": test.get("status"),
        "result_status": test.get("result_status"),
        "measured_value": result.get("measured_value"),
        "limit_value": result.get("limit_value"),
    }


def _test_changes(before: dict, after: dict) -> list[dict]:
    changes = []
    old_tests, new_tests = _by_code(before), _by_code(after)
    for code in sorted(set(old_tests) | set(new_tests)):
        old, new = old_tests.get(code), new_tests.get(code)
        if old is None or new is None:
            changes.append(
                {
                    "area": "tests",
                    "code": code,
                    "field": "record",
                    "before": _test_label(old),
                    "after": _test_label(new),
                }
            )
            continue
        for field, old_value, new_value in _leaf_differences(old, new):
            changes.append(
                {
                    "area": "tests",
                    "code": code,
                    "field": field,
                    "before": old_value,
                    "after": new_value,
                }
            )
    return changes


def compare_snapshots(before: dict, after: dict) -> dict:
    """Return a structured comparison of two report snapshots.

    ``before`` and ``after`` are the snapshots recorded for two revisions of
    the same report. The result lists every field that differs, grouped by
    area, and names the tests that changed and those that did not.
    """
    before = before or {}
    after = after or {}
    changes: list[dict] = []
    for area in UNCHANGED_AREAS:
        changes.extend(_area_changes(area, before, after))
    changes.extend(_list_changes("conditions", before.get("conditions"), after.get("conditions")))
    changes.extend(_list_changes("evidence", before.get("evidence"), after.get("evidence")))
    changes.extend(_test_changes(before, after))

    changed_tests = sorted({item["code"] for item in changes if item["area"] == "tests" and item["code"]})
    all_tests = sorted(set(_by_code(before)) | set(_by_code(after)))
    return {
        "report_no": (after.get("meta") or {}).get("report_no")
        or (before.get("meta") or {}).get("report_no"),
        "before": _revision_label(before),
        "after": _revision_label(after),
        "change_count": len(changes),
        "changes": changes,
        "changed_tests": changed_tests,
        "unchanged_tests": [code for code in all_tests if code not in changed_tests],
    }
