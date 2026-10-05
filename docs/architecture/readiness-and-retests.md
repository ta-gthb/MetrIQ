# Readiness, explanations and re-tests

Audit item 16: *transparency and readiness.* Three questions have to be
answerable from the record itself, before anyone signs anything:

* **Is this case ready?** What, exactly, still stands between it and technical
  review? Where can the reader see the completion of the plan?
* **Why is this test PASS or FAIL?** Which rule decided it, which row governed
  it, what was measured against which limit, and what should be investigated?
* **How is a wrong measurement corrected?** Without overwriting the record that
  was already signed, and without losing what was measured before.

`app/services/readiness.py` answers all three from the same rows the calculation
engine wrote. Nothing is recomputed for display, and nothing is inferred from a
later state of the case.

## 1. Readiness: `GET /api/v1/cases/{case_id}/readiness`

The response is a list of what is missing rather than a boolean:

| Field | Contents |
| --- | --- |
| `completion` | tests in the live plan, applicable, completed, pending, superseded, percent |
| `ready_for_review` | true only when `blocking` is empty |
| `blocking` | one entry per reason the case cannot be submitted: an unresolved test, an unresolved result, a failed test without a waiver, a missing applicability reason, outstanding evidence |
| `warnings` | things that must be seen but do not refuse submission: no environmental conditions recorded, no test equipment recorded, engine warnings on a stored result |
| `missing_data` | per test, the dry-run answer to "what input is still needed" (see below) |
| `evidence` | the evidence checklist state (categories required, attached, missing) |
| `environment`, `equipment` | what has been recorded for the case |
| `plan_scope` | what the plan covers and what the catalogue defines besides, with the reason (section 4) |
| `summary` | the same aggregate the dashboard and report use (`summarise_case`) |

The submission gate in `app/routers/workflow.py` applies the same blocking
rules, so a case cannot pass the gate that the readiness view reports as
blocked. The view exists so the refusal is never the first time the operator
learns of it. `tests/test_readiness.py` asserts that the gate and the view agree.

### Dry-run missing data

`outstanding_inputs()` calls the calculation engine with `persist=False`. No
calculation run is stored, no result is written, and the answer is the engine's
own `MissingInputError`/`ValidationError` message - the same message the
operator would get from `POST /tests/{id}/calculate`, published early.

## 2. Why a test has its result: `GET /api/v1/tests/{test_id}/explanation`

The explanation is read back from the superseded-or-live `CalculationRun` and
`ComplianceResult` of that test record:

* `decision` - status, measured value, limit, margin, unit, rule id, rule
  version, clause reference, evaluation timestamp;
* `method` - the calculation rules, compliance rules and validation rules the
  definition carries, the rounding policy and the engine version;
* `why` - ordered sentences: the procedure and the rule that evaluated it, the
  governing row and its margin, the rows outside their limit, the recorded value
  against the recorded limit;
* `steps` - the stored scalar intermediates (method, governing row, spread,
  correction, tolerance), in the order the engine recorded them;
* `rows` - the row table the decision was taken from, with each row's error,
  limit, margin and `within` flag;
* `outstanding` - the dry-run answer above when the test has no resolved result;
* `investigation` - what to do next, given the result: re-read the rows, correct
  the input as a new calculation, re-test, or record an approved waiver;
* `pending_review_limits` - limits this test is judged against that are still
  project proposals (section 5);
* `retest` - whether a re-test may be started, the current revision number and
  the link to the record it replaced;
* `revisions` - every revision of this test definition in the case, oldest
  first, with its result and supersession state.

## 3. Re-tests: `POST /api/v1/tests/{test_id}/retest`

A correction is a new revision, never an overwrite:

1. The request carries a `reason` (5-2000 characters). A re-test without a
   reason is refused with `422`.
2. The record being replaced keeps its observations, its calculation runs and
   its result. It gains `superseded_at` and `superseded_by_test_instance_id`.
3. A new `test_instances` row is created with `revision_no + 1`,
   `supersedes_test_instance_id` pointing at the old row, `status =
   NOT_STARTED`, `result_status = PENDING` and the reason copied onto it.
4. A `RETEST` audit event records the actor, the reason, the superseded result
   and the new revision number.

A re-test is refused with `409` when the case has moved past measurement
(`SUBMITTED`, `UNDER_REVIEW`, `APPROVED`, `PUBLISHED`, `CANCELLED`), when the
record has already been superseded, or when the test is waived.

### What a superseded record means downstream

| Consumer | Behaviour |
| --- | --- |
| `summarise_case` | counts only live tests; publishes `superseded` beside them |
| Case detail (`GET /cases/{id}`) | lists live tests in `tests` and the superseded ones in `superseded_tests` |
| Test list (`GET /cases/{id}/tests`) | live tests by default; `?include_superseded=true` adds the history |
| Readiness | the replacement is the test; the superseded row is never blocking |
| Report snapshot | `tests` holds the live revision of each definition (with `revision_no`); `superseded_tests` records what was replaced, and `meta.superseded_tests` counts them |
| PDF/DOCX | the summary table gains a "Superseded re-tests" line when the count is non-zero |

The report is a record of the evaluation as it stands: the live revision of each
test decides, and the fact that a measurement was replaced (with its reason) is
part of what a reader is told.

## 4. Plan scope: a gap is never silent

`plan_scope` separates the definitions in the case plan from the catalogue
entries the plan does not run, each with a reason:

* `implementation_status = not_implemented` - defined but not executable in this
  release;
* `is_active = false` - implemented and carried in the catalogue, but disabled
  in this ruleset version;
* anything else - the stored plan was generated from a different set of
  definitions, which the reader should see rather than infer.

Only active ruleset versions are available for new evaluations. A System
Administrator activates or deactivates a version in Standards & rules; the
readiness view explains when an individual catalogue procedure is disabled.

## 5. Automated coverage

`tests/test_readiness.py` covers the whole contract: a fresh case reports what
it is waiting for; a fully recorded case reports ready with 100 percent; the
case detail hides superseded records; the explanation of a PASS names the
governing row, the clause and the margin; the explanation of an unfinished test
names the missing input; procedures outside the plan are named with their
reason; a re-test supersedes without deleting; a re-test needs a reason, is
refused after submission and cannot be applied twice; the replacement is
recalculated under its new revision and the case stays submittable; and the
report shows the live revision while recording what it superseded.
