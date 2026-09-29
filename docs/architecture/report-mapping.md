# Report mapping and golden fixtures

Audit item 9: *even when calculations are correct, incorrect report mapping can
put the wrong result or clause text into the final document.*

The chain that has to hold is:

```
clause -> test -> rule -> calculation -> compliance -> report section -> rendered document
```

Three things enforce it.

## 1. The mapping is data, and the data is checked

`app/rules/data/test-catalogue-r76-1-2006-v1.json` declares, for every test, its
`clause_reference` and its `report_section_mapping` (`section`, `number`,
`title`). `tests/test_test_catalogue.py` asserts that each mapping names a
section the R 76-2 template actually defines, that section numbers are unique,
and that the individual test records are numbered in catalogue order.

`tests/test_coverage_matrix.py` asserts that the committed
[a coverage matrix](oiml-coverage-matrix.md) still describes the rule data, the
catalogue and the registered calculators. The matrix is the artefact a metrology
reviewer reads; the test is what stops it going stale.

## 2. Golden fixtures

`tests/test_report_mapping.py` records a deterministic multi-range case and
compares three generated artefacts against committed fixtures:

| Fixture | Contents |
| --- | --- |
| `tests/fixtures/report_golden/expected_snapshot.json` | The report data snapshot (the single input both renderers read): clause references, result statuses, measured values with units, limits, margins, rule ids and versions, row detail, summary counts |
| `tests/fixtures/report_golden/expected_docx.txt` | The generated DOCX, read back in document order (paragraphs and table rows) |
| `tests/fixtures/report_golden/expected_pdf.txt` | The generated PDF, with text extracted page by page |

The case is deliberately awkward: a two-range instrument with different scale
intervals per range, repeated observations at one load, a zero-corrected error,
and a failing row. Each of those is a place where a mapping mistake produces a
plausible-looking wrong report.

Identifiers and timestamps are normalised before comparison
(`tests/report_fixture.py::scrub`), so the fixtures pin *content*, not
incidental values.

To refresh the fixtures after an intentional change:

```powershell
cd backend
$env:METRIQ_UPDATE_GOLDEN = "1"
python -m pytest tests/test_report_mapping.py -q
Remove-Item Env:METRIQ_UPDATE_GOLDEN
```

Review the diff before committing it: a changed fixture is a changed report.

## 3. Every displayed value is traceable

The tests also assert, without fixtures:

- each test block carries the same clause reference as the catalogue entry it
  came from;
- every decided result has a status, a rule version, a clause reference and a
  written explanation — there is no bare number in the report;
- a test that measures a mass publishes the unit it was measured in (a
  checklist, which reports a count of non-conforming items, has none);
- the range each weighing row was measured in survives into the report, and each
  range is judged with its own scale interval;
- the failing row drives the reported status and the reported measured value;
- the summary counts agree with the test blocks they summarise;
- the row structure is exactly the engine's output shape, so no template layer
  can introduce a field of its own.

## 4. Content hash

`meta.content_hash` is a SHA-256 over the canonical snapshot with the volatile
metadata excluded (`content_hash()` in `app/services/report_engine/snapshot.py`).
Two consequences matter:

- the same case records always produce the same hash and verification code, so a
  reprint verifies against the original;
- the hash identifies the *content*, not the moment it was rendered.

Approval is recorded as a workflow decision with the named reviewer and
approver. There is no cryptographic signature: the report is **approved and
hash-verifiable**, and `tests/test_report_mapping.py` plus
`backend/scripts/check_release.py` both keep it described that way.
