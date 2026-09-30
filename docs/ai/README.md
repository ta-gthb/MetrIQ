# AI Documentation

MetrIQ uses AI for five bounded, advisory tasks. The controlling rule is
**AI never decides compliance**: it extracts, warns, classifies and explains;
a human always confirms, and the deterministic engine alone produces
`PASS`/`FAIL` (PRD 12.5, 27.2).

## 1. Supported features

| Code (API) | Setting key | What it does | Where it is used |
| --- | --- | --- | --- |
| `nameplate_ocr` | `ai.nameplate_extract` | Reads model, type designation, serial number, class, Max, Min, `e`, `d`, unit from a nameplate photo/PDF | Evaluation step 16 (instrument) |
| `anomaly_detection` | `ai.anomaly_detection` | Robust statistics over an observation table; flags implausible rows | Test execution, right-hand panel |
| `document_classification` | `ai.document_classification` | Suggests an evidence category from filename/MIME/caption | Evidence upload |
| `r76_assistant` | `ai.knowledge_assistant` | Retrieval-grounded answers with citations | `/assistant.html` |
| `report_consistency` | `ai.report_consistency` | Compares the recorded case with the report content: required rows, units, loads above Max, condition sequences, evidence links and version references. Every finding cites the field it came from. | Step 22 (review), `POST /api/v1/ai/report-consistency` |

Enable or disable each feature with `PATCH /api/v1/ai/features/{code}?enabled=true|false`
(requires `ai.manage`). Toggles are stored as system settings and take effect per
request.

## 2. Provider and model abstraction

```
app/services/ai_service/
  base.py            dataclasses + AIProvider protocol + AIUnavailable
  stub_provider.py   deterministic, offline implementation (default)
  openai_provider.py OpenAI-compatible HTTP client (optional)
  service.py         get_ai_service(db, actor) -> cached provider bound to a DB session
```

The protocol is:

```python
class AIProvider(Protocol):
    def extract_nameplate(self, *, image_bytes, filename, context) -> ExtractionResult: ...
    def detect_anomaly(self, *, test_code, rows, instrument) -> AnomalyReport: ...
    def classify_document(self, *, filename, content_type, caption) -> ClassificationResult: ...
    def answer_r76(self, *, question, sources) -> AssistantAnswer: ...
    def check_report_consistency(self, *, context) -> ConsistencyReport: ...
```

`AI_PROVIDER=stub` is the default and requires no network access: anomaly
detection uses median/MAD and decimal-shift tests, classification uses keyword
and MIME heuristics, and the assistant performs keyword retrieval over the
configured rules, test catalogue and report template. This keeps the demo and
the test suite deterministic and offline.

Switching to `openai` (or an OpenAI-compatible gateway via `AI_BASE_URL`) only
changes the provider implementation; the routers, governance and audit trail are
unchanged.

## 3. Data sent to the AI provider

| Feature | Data | Notes |
| --- | --- | --- |
| Nameplate extraction | The uploaded image/PDF bytes, the filename, and non-identifying instrument context | Only the file the user selected |
| Anomaly detection | The observation rows (loads, indications, times, temperatures) and instrument characteristics | No applicant or personal data |
| Classification | Filename, MIME type and caption | No file content |
| R 76 assistant | The question and the retrieved configuration excerpts | Retrieval is local; excerpts come from rule data only |
| Report consistency | Nothing leaves the deployment | The check runs locally over the case records; the context carries applicant data and is never sent to a hosted model |

Privacy requirements (PRD 19.2): no applicant personal data or contact details
are sent, uploads are never used for training, and the stub provider sends
nothing at all.

## 4. Human verification points

| Feature | Verification |
| --- | --- |
| Nameplate extraction | Fields are shown beside the recorded instrument values with a confidence indicator; nothing is written to the record automatically. Low-confidence fields are highlighted. |
| Anomaly detection | Findings are advisory; each open finding requires a disposition (`confirmed` / `dismissed`) recorded in the audit trail. Observations and compliance results are never modified by a disposition. |
| Classification | The suggested category is applied as `category_source = "ai"` and is always correctable via `PATCH /attachments/{id}`. |
| Assistant | Answers carry citations and a `grounded` flag; an ungrounded answer is labelled and explicitly states that the platform does not invent rules. |
| Report consistency | Findings are advisory and cite the report field they came from. A human decides what to correct; the check never writes a value, changes a result or approves anything. |

`GET /dashboard/ai-review` lists low-confidence extractions and open anomaly
warnings awaiting a disposition.

## 5. Confidence handling

* Every extraction field carries `confidence` and `low_confidence`
  (`confidence < AI_MIN_CONFIDENCE`, default `0.75`).
* Classification results carry a confidence and up to three alternatives.
* Anomaly findings carry a severity (`low` / `medium` / `high`) and a score.
* Low-confidence output is never silently promoted: it is surfaced for review
  and counted in the dashboard AI queue.

## 6. Audit events

Every AI call writes an `ai_events` row containing the feature code, provider,
model, a summary of the output, confidence, the case/test reference and the
acting user. A human disposition writes the disposition and timestamp on the
same row plus an `AI_ACTION` audit-log entry. Nothing is deleted, so the full
"AI suggested X, human decided Y" chain is reconstructable (PRD 12.5).

## 7. Known limitations

* The stub provider's accuracy is heuristic; it is intended for demos and tests,
  not for production metrology judgements.
* Nameplate extraction depends on image quality; glare, rotation and partial
  nameplates reduce confidence and may return no fields.
* Anomaly detection flags statistical outliers, not instrument faults. A flagged
  row may be a legitimate measurement and can be dismissed with a note.
* Classification covers the configured evidence categories only; anything else
  falls back to `other`.
* The assistant answers only from versioned configuration data; it will refuse
  rather than invent a rule when nothing matches.
* Report-consistency findings are structural and deterministic: they flag gaps
  and contradictions in the records (for example a load above Max or a missing
  zero-load row), not metrological errors, and a reviewer still decides.
* No AI output is a substitute for the controlled copy of OIML R 76 or for the
  laboratory's approved procedures.

## 8. Disable and fallback behaviour

* `AI_ENABLED=false`, or an individual feature toggle set to `false`, makes the
  corresponding endpoint return `available: false` with an explanatory message.
* If a configured provider times out (`AI_TIMEOUT_SECONDS`) or errors, the
  service degrades that single call; it never raises into the workflow.
* Calculation, validation, workflow, reporting, evidence upload and download
  remain fully usable with AI disabled - this is asserted by
  `backend/tests/test_api_ai.py`.
* `GET /health` reports `calculation_engine_independent_of_ai: true` so a
  deployment can be checked at a glance.