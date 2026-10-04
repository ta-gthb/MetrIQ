"""Document verification: the QR mark, the public check and its budget.

Audit item 14 asks for a verification page and an immutable link between a
printed report and the record it came from. What that link can honestly claim
is narrow: the stored snapshot still hashes to the value printed on the
document. These tests hold the claim, the mark and the endpoint to that.
"""

from __future__ import annotations

import struct
import uuid
import zlib

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.database import SessionLocal
from app.models import AuditLog, EvaluationCase
from app.security import rate_limit
from app.security.permissions import APPROVER
from app.services.report_engine.snapshot import build_report_snapshot, content_hash
from app.services.report_engine.verification import (
    png_bytes,
    qr_matrix,
    verification_url,
)

from tests.conftest import png_bytes as fixture_png
from tests.report_fixture import build_golden_case

API = "/api/v1"


def _decode_png(payload: bytes) -> tuple[int, int, bytes]:
    """Decode the PNG this project writes, checking every chunk's CRC."""
    assert payload[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    offset = 8
    width = height = 0
    compressed = b""
    while offset < len(payload):
        (length,) = struct.unpack(">I", payload[offset:offset + 4])
        kind = payload[offset + 4:offset + 8]
        body = payload[offset + 8:offset + 8 + length]
        (crc,) = struct.unpack(">I", payload[offset + 8 + length:offset + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF, f"bad CRC on {kind!r}"
        if kind == b"IHDR":
            width, height, depth, colour, *_ = struct.unpack(">IIBBBBB", body)
            assert (depth, colour) == (8, 2), "expected an 8-bit truecolour image"
        elif kind == b"IDAT":
            compressed += body
        offset += 12 + length
    raw = zlib.decompress(compressed)
    assert len(raw) == height * (1 + width * 3)
    return width, height, raw


def _pixel(raw: bytes, width: int, x: int, y: int) -> int:
    return raw[y * (1 + width * 3) + 1 + x * 3]


# ------------------------------------------------------------------- mark ---

def test_the_mark_is_a_valid_png_with_one_pixel_group_per_module():
    payload = "https://metriq.example/verification.html?code=ABCDEF123456"
    rows = qr_matrix(payload)
    scale, quiet = 3, 4
    png = png_bytes(rows, scale=scale, quiet=quiet)
    width, height, raw = _decode_png(png)

    assert width == height == (len(rows) + 2 * quiet) * scale
    # The quiet zone is white on every edge.
    assert _pixel(raw, width, 0, 0) == 255
    assert _pixel(raw, width, width - 1, height - 1) == 255
    # The first finder pattern's top-left module is dark; its neighbourhood is
    # mixed, which is what makes it recognisable as a real QR and not a blob.
    assert _pixel(raw, width, quiet * scale, quiet * scale) == 0
    assert rows[0][0] is True

    row, col = next(
        (row, col)
        for row in range(len(rows))
        for col in range(len(rows))
        if not rows[row][col]
    )
    assert _pixel(raw, width, (col + quiet) * scale + 1, (row + quiet) * scale + 1) == 255


def test_a_report_only_carries_a_mark_when_the_deployment_knows_its_address(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_APP_URL", None)
    assert verification_url("ABCDEF123456") is None

    monkeypatch.setattr(settings, "PUBLIC_APP_URL", "https://metriq.vercel.app/")
    assert verification_url("abcdef123456") == (
        "https://metriq.vercel.app/verification.html?code=ABCDEF123456"
    )
    assert verification_url(None) is None


def test_the_public_address_is_not_part_of_the_content_hash(case_factory, monkeypatch):
    """Two instances holding the same records must print the same code.

    The address is deployment metadata, so it is rendered into the document but
    excluded from the hash: otherwise the same result would verify differently
    depending on where it was generated.
    """
    case_id = case_factory()["id"]
    with SessionLocal() as db:
        case = db.get(EvaluationCase, uuid.UUID(case_id))
        monkeypatch.setattr(settings, "PUBLIC_APP_URL", None)
        without, digest_without = build_report_snapshot(db, case=case, report_no="RPT-TEST-00001")
        monkeypatch.setattr(settings, "PUBLIC_APP_URL", "https://metriq.vercel.app")
        with_url, digest_with_url = build_report_snapshot(db, case=case, report_no="RPT-TEST-00001")

    assert without["meta"]["verification_url"] is None
    assert digest_without == digest_with_url
    assert content_hash(with_url) == digest_with_url
    assert with_url["meta"]["verification_url"].endswith(
        f"code={with_url['meta']['verification_code']}"
    )


# --------------------------------------------------------------- endpoint ---

@pytest.fixture
def issued_report(client, tokens, case_factory):
    """A recorded, conforming case with a generated PDF.

    The reference case in tests/report_fixture is deliberately failing, and a
    failed evaluation cannot be submitted; this fixture builds a passing one so
    the verification path is exercised against a report that could be released.
    """
    case_id = case_factory()["id"]
    submitted = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens["ENGINEER"])
    assert submitted.status_code == 200, submitted.text
    verified = client.post(f"{API}/cases/{case_id}/verify", json={}, headers=tokens["REVIEWER"])
    assert verified.status_code == 200, verified.text
    approved = client.post(
        f"{API}/cases/{case_id}/approve",
        json={"reason": "Conforming evaluation released for the verification fixture."},
        headers=tokens[APPROVER],
    )
    assert approved.status_code == 200, approved.text
    response = client.post(
        f"{API}/cases/{case_id}/reports/generate",
        json={"formats": ["pdf"]},
        headers=tokens[APPROVER],
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_printed_report_can_be_checked_without_signing_in(client, issued_report):
    code = issued_report["verification_code"]
    response = client.get(f"{API}/verify/{code.lower()}")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["verified"] is True
    assert body["hash_matches"] is True
    assert body["code"] == code
    assert body["report_no"] == issued_report["report_no"]
    assert body["revision_no"] == issued_report["revision_no"]
    assert body["content_hash"] == issued_report["content_hash"]
    assert body["standard"].startswith("OIML R 76-1")
    assert body["overall_result"] in {"PASS", "FAIL", "INCOMPLETE"}
    # A check confirms the document; it does not publish the case.
    for withheld in ("tests", "evidence", "observations", "applicant", "instrument"):
        assert withheld not in body


def test_an_unknown_code_is_indistinguishable_from_a_malformed_one(client):
    unknown = client.get(f"{API}/verify/{'A' * 12}")
    malformed = client.get(f"{API}/verify/not-a-code!")
    assert unknown.status_code == malformed.status_code == 404
    assert unknown.json()["detail"] == malformed.json()["detail"]


def test_a_scan_is_recorded_against_the_report(client, issued_report):
    scanned = client.get(f"{API}/verify/{issued_report['verification_code']}")
    assert scanned.status_code == 200, scanned.text

    db = SessionLocal()
    try:
        rows = db.query(AuditLog).filter(AuditLog.event_type == "VERIFY").all()
    finally:
        db.close()
    assert rows, "a scan must leave a trace"
    # entity_id is stored as text, like every other audit reference.
    assert issued_report["id"] in {row.entity_id for row in rows}, (
        "the scan must be recorded against the report that was checked"
    )
    row = next(item for item in rows if item.entity_id == issued_report["id"])
    assert row.actor_id is None, "a public scan has no actor to attribute"


def test_the_public_endpoint_stops_answering_after_its_budget():
    rate_limit.reset()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/verify/X",
            "headers": [],
            "client": ("203.0.113.9", 4321),
        }
    )
    try:
        for _ in range(rate_limit.VERIFY.limit):
            rate_limit.check(request, rate_limit.VERIFY)
        with pytest.raises(HTTPException) as caught:
            rate_limit.check(request, rate_limit.VERIFY)
        assert caught.value.status_code == 429
        assert int(caught.value.headers["Retry-After"]) >= 1
    finally:
        rate_limit.reset()