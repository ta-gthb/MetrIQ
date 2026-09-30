"""Section numbering and titles for the report, taken from the template.

OIML R 76-2 prescribes the report's structure. The numbers and the wording of
each section are read from the activated report template rather than written
into the renderers, so a template revision renumbers the report instead of the
two renderers drifting apart - which is what had happened: the DOCX called the
evidence section 7 while the PDF called it 8, and neither followed the template
they were both rendering.

A template that carries no section map, or no entry for a key, falls back to
the renderer's own wording and numbering, so a report still renders.
"""

from __future__ import annotations

FALLBACK_NUMBERS = {
    "cover": "0",
    "applicant": "1",
    "instrument": "2",
    "conditions": "3",
    "equipment": "4",
    "summary": "5",
    "tests": "6",
    "checklist": "7",
    "evidence": "8",
    "review": "9",
    "versions": "10",
    "verification": "11",
}


def section_entries(snapshot: dict) -> dict[str, dict]:
    """The template's section map, keyed by section key."""
    sections = (snapshot.get("versions") or {}).get("template_sections") or []
    lookup: dict[str, dict] = {}
    for entry in sections:
        if isinstance(entry, dict) and entry.get("key"):
            lookup[str(entry["key"])] = entry
    return lookup


def section_number(snapshot: dict, key: str, fallback: str | None = None) -> str:
    entry = section_entries(snapshot).get(key) or {}
    number = str(entry.get("number") or "").strip()
    if number:
        return number
    return fallback or FALLBACK_NUMBERS.get(key, "")


def section_required(snapshot: dict, key: str) -> bool:
    """Whether the template in force requires this section.

    An optional section with nothing to report is left out of the
    document; a required one is rendered even when it is empty, because
    the reader has to see that it was considered.
    """
    entry = section_entries(snapshot).get(key)
    if entry is None or "required" not in entry:
        return True
    return bool(entry.get("required"))


def section_layer(snapshot: dict, key: str) -> str:
    """``"oiml_r76_2"`` for the prescribed format, ``"enhanced"`` for ours.

    R 76-2 defines the report format; MetrIQ adds a traceability layer on
    top of it. Which section belongs to which is declared by the template,
    so the distinction is checkable rather than a claim in a brochure.
    """
    entry = section_entries(snapshot).get(key) or {}
    return str(entry.get("layer") or "oiml_r76_2")


def heading_text(snapshot: dict, key: str, fallback: str) -> str:
    """``"8. Evidence and attachments"`` for the template in force."""
    entry = section_entries(snapshot).get(key) or {}
    title = str(entry.get("title") or "").strip() or fallback
    number = section_number(snapshot, key)
    return f"{number}. {title}" if number else title