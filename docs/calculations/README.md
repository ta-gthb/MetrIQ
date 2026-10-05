# Calculation and Compliance Reference

This document defines, for each MVP test, the inputs, the formula, the units,
the rounding rule, the OIML clause the configuration cites, worked examples and
boundary examples. It describes what the implementation in
`backend/app/services/calculation_engine/` and `compliance_engine/` actually
does; it is **not** a substitute for the controlled standard.

> Ruleset activation controls whether a version applies to new evaluations. A
> System Administrator activates or deactivates versions in Standards & rules.
> Activation does not certify the legal interpretation of the versioned source
> data; rule definitions remain the developer-supplied configuration.

## 1. Common conventions

### 1.1 Arithmetic and precision

* All arithmetic uses `decimal.Decimal` with the default 28-significant-digit
  context. Floats are never used for a compliance decision.
* Values are stored in `Numeric` columns and serialised to JSON as plain
  decimal strings (never scientific notation), e.g. `"10.00000000"`.
* A result carries `engine_version` (`2.0.0`) and `rule_version`
  (e.g. `r76-1-2006-v1`) so any figure can be reproduced.

### 1.2 Units

* The instrument declares a mass unit (`g`, `kg`, `mg`, `t`, `lb`, `oz`); `e`
  and `d` are expressed in that unit.
* An observation row may override the unit with its own `unit` column. Loads are
  converted into the instrument unit before the error is computed, so mixed-unit
  rows are normalised deterministically (`app/utils/units.py`).
* Tolerance bands expressed in `e` are converted to instrument units by
  multiplying the factor by `e`.

### 1.3 Rounding policies

| Policy | Meaning |
| --- | --- |
| `EXACT-NO-ROUNDING` | Default. The comparison uses the exact decimal value. |
| `R76-HALF-UP-TO-RESOLUTION` | Rounds to the instrument resolution (one `d`, or `0.1 e` for verification tests) using half-up, then compares. |

The policy used for a run is returned in `rounding_policy` on every calculation
and is frozen into the report snapshot.

### 1.4 Comparators

| Comparator | Meaning |
| --- | --- |
| `abs_lte` | `abs(measured) <= limit` |
| `lte` | `measured <= limit` |
| `gte` | `measured >= limit` |
| `eq` | `measured == limit` |
| `range` | `low <= measured <= high` |

`PASS` is produced only when the comparator is satisfied. `FAIL` means the
comparator was not satisfied. The engine additionally emits `INCOMPLETE` (input
missing) and `INVALID` (an input could not be parsed or a rule is missing);
neither is a compliance verdict. `NOT_APPLICABLE` and `WAIVED` come only from an
explicit, reason-bearing human action.

## 2. MPE bands (OIML R 76-1:2006 Table 1, initial verification)

Resolution policy: `m = load / e`. **The first band whose upper bound is not
exceeded wins**, so a boundary value such as `m = 500` belongs to the band
`0 <= m <= 500`.

| Class | Band 1 | `MPE` | Band 2 | `MPE` | Band 3 | `MPE` |
| --- | --- | --- | --- | --- | --- | --- |
| I | 0 <= m <= 50000 | 0.5 e | 50000 < m <= 200000 | 1.0 e | m > 200000 | 1.5 e |
| II | 0 <= m <= 5000 | 0.5 e | 5000 < m <= 20000 | 1.0 e | m > 20000 | 1.5 e |
| III | 0 <= m <= 500 | 0.5 e | 500 < m <= 2000 | 1.0 e | m > 2000 | 1.5 e |
| IIII | 0 <= m <= 50 | 0.5 e | 50 < m <= 200 | 1.0 e | m > 200 | 1.5 e |

Rule ids are of the form `R76-MPE-<class>-<band>`, e.g. `R76-MPE-III-02` for a
class III instrument in band 2. Every band carries
`clause_reference = "OIML R 76-1:2006 Table 1"`.

In-service (subsequent verification) bands are also present in the rule data at
`3x` the initial-verification allowances; the verification stage is selected
with the `stage` parameter of `POST /calculations/mpe`.

An at-zero allowance of `0.25 e` for electronic instruments is configured in
the rule set (`R76-MPE-ZERO`).

## 3. Error of indication (used by T-WP, T-REP, T-ECC)

### 3.1 Formula

```
P = I + 0.5 e - dL          (indication converted to the "before rounding" value)
E = P - L                   (error of indication)
E_c = E - E0                (error corrected for the zero error, when available)
```

* `L`  applied load in instrument units
* `I`  indication at that load
* `dL` additional load (e.g. the changeover weights used to bring the
  indication to the next step); assumed `0` when the test definition does not
  require it
* `E0` error at load zero (the first row with `L = 0`), used when
  `apply_zero_correction` is enabled

The governing row is the one with the **least compliance margin** against its
own MPE (`margin = MPE - abs(E_c)`), not the largest absolute error: across MPE
bands a 16 g error against a 15 g limit is a worse non-conformity than a 25 g
error against a 45 g limit. Its `E_c` is the reported `measured_value` and its
band MPE is `limit_value`. The zero row that establishes `E0` is checked but is
not eligible to govern, because its error is subtracted from every other row.

Two rules always apply on top of the comparison:

* **Every required row must pass.** If any row is outside its own limit the test
  is `FAIL`, whatever the governing row says (`failed_rows` is reported in the
  intermediates).
* **The required rows must exist.** `T-WP` requires a zero-load reading
  (`required_rows` in the catalogue), otherwise the zero correction could be
  skipped silently and the test cannot be evaluated.

### 3.2 Worked example - class III, Max 30000 g, e = d = 10 g

From the demo dataset (`T-WP`, `R76-MPE-III-*`):

| Row | L (g) | I (g) | dL (g) | P = I + 0.5e - dL | E (g) | m = L/e | Band | MPE | Margin | Within |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 (Zero) | 0 | 0 | 5 | 0 + 5 - 5 = 0 | 0 | 0 | 0 <= m <= 500 | 5 | +5 | yes |
| 2 | 5000 | 5003 | 2 | 5003 + 5 - 2 = 5006 | 6 | 500 | 0 <= m <= 500 | 5 | -1 | no |
| 3 | 10000 | 10002 | 3 | 10002 + 5 - 3 = 10004 | 4 | 1000 | 500 < m <= 2000 | 10 | +6 | yes |
| 6 | 25000 | 25004 | 1 | 25004 + 5 - 1 = 25008 | 8 | 2500 | m > 2000 | 15 | +7 | yes |
| 7 | 30000 | 30005 | 0 | 30005 + 5 - 0 = 30010 | 10 | 3000 | m > 2000 | 15 | +5 | yes |

Row 1 is the zero row, so `E0 = 0` and `E_c = E` everywhere. Row 2 has the
least margin (`-1 g`), so it governs: the outcome is **FAIL** with
`measured_value = 6`, `limit_value = 5`, `margin = -1`,
`rule_id = R76-MPE-III-02`. The rows that pass are still shown with their own
margins; the per-row verdicts and the governing verdict are both visible, and
the test can only pass when every row passes.

### 3.3 Boundary examples

| Load | e | m = L/e | Band selected | Why |
| --- | --- | --- | --- | --- |
| 5000 g | 10 g | 500 | `0 <= m <= 500` | upper bound is inclusive |
| 5000.1 g | 10 g | 500.01 | `500 < m <= 2000` | first band exceeded |
| 20000 g | 10 g | 2000 | `500 < m <= 2000` | upper bound inclusive |
| 20000.1 g | 10 g | 2000.01 | `m > 2000` | first two bands exceeded |
| 0 g | 10 g | 0 | `0 <= m <= 500` | zero load uses band 1 |

## 4. Per-test catalogue

Reference: `GET /test-definitions` (catalogue) and `GET /rules` (rule data).

| Code | Test | Measured quantity | Limit | Rule id | Cited clause |
| --- | --- | --- | --- | --- | --- |
| `T-WP` | Weighing performance | least-margin row of `E_c` | MPE at that row's load | `R76-MPE-<class>-<band>` | R 76-1:2006 T.3.1 / A.4.4, Table 1 |
| `T-REP` | Repeatability | every repetition AND the spread `= max(E) - min(E)` per load group | MPE at that load | `R76-REP-01` | R 76-1:2006 3.5.2 / T.3.2 |
| `T-ECC` | Eccentricity | `abs(E_position - E_reference)` | MPE at applied load | `R76-ECC-01` | R 76-1:2006 T.3.3 |
| `T-ZR` | Zero return | `value(after unloading) - value(initial zero)` | `0.5 e` | `R76-ZR-01` | R 76-1:2006 4.5.4 / T.3.2.1 |
| `T-CREEP` | Creep | `value(t_i) - value(t_0)`, constant load | `0.5 e` | `R76-CREEP-01` | R 76-1:2006 4.5.5 / T.3.3 |
| `T-TEMP-NL` | Temperature effect on no-load indication | `value(T_i) - value(T_ref)`, unloaded | `1.0 e` | `R76-TEMP-NL-01` | R 76-1:2006 4.5.2 / T.3.4.2 |
| `T-SENS` | Sensitivity | indication difference between two loads | `1.0 e` | `R76-SENS-01` | R 76-1:2006 A.4.4.2 / T.3.5 |
| `T-DISC` | Discrimination | smallest indication change | `1.0 d` (`gte`) | `R76-DISC-01` | R 76-1:2006 4.5.6 / T.3.6 |
| `T-STAB` | Stability of equilibrium | `value(t_i) - value(t_0)` | `1.0 e` | `R76-STAB-01` | R 76-1:2006 4.5.3 / T.3.7 |
| `T-CHK-CON` | Examination of construction | boolean conformance of each item | all mandatory items conform | `R76-CONST` | R 76-1:2006 6 / T.4 |
| `T-CHK-ID` | Identification and markings | boolean conformance of each item | all mandatory items present and consistent | `R76-MARKING` | R 76-1:2006 7 / T.4 |
| `T-TILT` | Tilting | `value(tilted) - value(reference position)` | MPE at the test load (`R76-TILT-01`) | `R76-TILT-01` | R 76-1:2006 4.4.4 / T.3.6 |
| `T-TARE` | Tare device (subtractive) | error of indication of the tared reading | `0.5 e` (`R76-TARE-01`) | `R76-TARE-01` | R 76-1:2006 4.6.4 / T.3.13 |
| `T-WARMUP` | Warm-up time | `value(t) - value(power-on)` | `1.0 e` (`R76-WARMUP-01`) | `R76-WARMUP-01` | R 76-1:2006 4.4.2 / T.3.8 |
| `T-VOLT` | Voltage variation, dips and interruptions | `value(supply condition) - value(nominal)` | `1.0 e` (`R76-VOLT-01`) | `R76-VOLT-01` | R 76-1:2006 4.4.3 / T.3.9 |
| `T-EMC` | Bursts, surges, ESD and RF immunity | `value(after disturbance) - value(before)` | `1.0 e` (`R76-EMC-01`) | `R76-EMC-01` | R 76-1:2006 4.4.5 / T.3.10 |
| `T-DAMP` | Damp heat, span stability and endurance | `value(after conditioning) - value(before)` | MPE at the test load (`R76-DAMP-01`) | `R76-DAMP-01` | R 76-1:2006 4.4.6 / T.3.11 |

These procedures are inactive in the catalogue until the associated ruleset is
activated by a System Administrator.

### 4.1 Sequence procedures (`T-ZR`, `T-CREEP`, `T-TEMP-NL`, `T-STAB`, `T-TILT`,
`T-TARE`, `T-WARMUP`, `T-VOLT`, `T-EMC`, `T-DAMP`)

Each procedure is implemented separately, because the standard defines a
different reference reading and a different record for each one. What they share
is the comparison: `deviation_i = value_i - value_reference`, compared with the
tolerance for that load. The reported `measured_value` is the deviation of the
least-margin row; at least two rows are required, and a single row yields
`INCOMPLETE`. In every one of these procedures **all** rows must pass.

| Procedure | Reference row | Additionally required |
| --- | --- | --- |
| `T-ZR` | the initial zero reading (`initial`, `zero`, `start`, no load) - not simply the first row | a reading after unloading, recorded after the reference (`required_rows`, `stage_order`) |
| `T-CREEP` | the earliest reading, by `elapsed_seconds` | `elapsed_seconds` on every row; a constant-load period of at least `minimum_duration_seconds` (900 s, provisional) |
| `T-WARMUP` | the reading at power-on (earliest `elapsed_seconds`) | `elapsed_seconds` on every row; at least `minimum_duration_seconds` (1800 s, provisional) |
| `T-TEMP-NL` | the reading nearest `reference_temperature_c` (20 C) | `temperature_c` on every row; a temperature span of at least `required_temperature_span_c` (10 C) |
| `T-STAB` | the first reading of the observation period | elapsed times when available (a warning is raised when they are not) |
| `T-TILT` / `T-VOLT` / `T-EMC` / `T-DAMP` | the stage named by `reference_row`, otherwise a labelled stage (`level`, `nominal`, `before ...`) | - |
| `T-TARE` | not applicable: it is the error of indication of the tared reading | a tared zero reading and at least one loaded reading |

Illustrative example (`T-ZR`, e = 10 g, limit 0.5 e = 5 g):

| Row | value (g) | deviation (g) | Within |
| --- | --- | --- | --- |
| 1 | 0 | 0 | yes |
| 2 | 3 | +3 | yes |
| 3 | -4 | -4 | yes |
| 4 | 6 | +6 | no |

`measured_value = 6`, `limit_value = 5`, `margin = -1`, outcome **FAIL**.

### 4.2 Repeatability (`T-REP`)

Rows are grouped by load. For each group,
`spread = max(E) - min(E)`, compared with the MPE for that load. The reported
value is the spread of the least-margin group and its limit is the MPE at that
group's load. At least two observations of the **same** load are required
(`min_repetitions`, default 2).

Two conditions must hold together (audit item 4):

* **every individual repetition** lies within the MPE for the load, and
* **the spread of every group** does not exceed that MPE.

Illustrative example (e = 10 g, L = 10000 g, MPE = 10 g):

| Row | I (g) | dL (g) | E (g) |
| --- | --- | --- | --- |
| 1 | 10003 | 5 | 3 |
| 2 | 10005 | 5 | 5 |
| 3 | 10009 | 5 | 9 |

`spread = 9 - 3 = 6 g <= 10 g` -> **PASS**, `measured_value = 6`,
`limit_value = 10`, `margin = 4`.

### 4.3 Eccentricity (`T-ECC`)

The reference position is the row whose label matches the configured
`reference_position`, otherwise the row labelled centre (`centre`, `center`,
`c`, zero position), otherwise the first row (recorded as an assumption). The
difference `E_position - E_reference` is compared with the MPE for the applied
load.

### 4.4 Discrimination (`T-DISC`)

Uses `gte`: the smallest indication change must be **at least** `1.0 d`. A
change of exactly `1 d` passes; `0.9 d` fails.

### 4.5 Checklists (`T-CHK-CON`, `T-CHK-ID`)

Each checklist item carries `item_code` and `conforms` (boolean). The test
passes only when every mandatory item conforms, and is `INCOMPLETE` with the
missing items named when one has not been recorded.

Some items are mandatory only on some instruments. The catalogue declares those
with `conditional_items`, and the condition is evaluated against the instrument
configuration (the same attributes the applicability expressions read):

| Item | Mandatory when |
| --- | --- |
| `CON-05` tare device | `instrument.has_tare_device` is true |
| `CON-08` software identification | `instrument.has_software` is true |
| `CON-10` battery state indication | `instrument.has_battery` is true |

When a conditional item is missing, the error names the condition as well as the
item, so the reason is visible in the record.

## 5. Applicability

Applicability is evaluated before any calculation (PRD 10.1). Expressions are
JSON trees evaluated by `app/rules/expressions.py` against a context built from
the instrument and the case. The instrument part of the context carries the
declared attributes (class, model, `e`, `d`, Max, Min, electronic, multi-range,
multi-interval, tare device, zero-setting device, level indicator, range table)
and the configuration-derived ones produced by
`app/services/instrument_profile.py`: self-indicating type, mains supply, battery,
interfaces, printer and embedded software. The same object is passed to the
deterministic engine, so a plan and its calculation can never disagree about the
instrument. For example `T-VOLT` applies only when the instrument is electronic
**and** mains-powered.

Example traces are returned in `applicability_trace`, e.g.
`"root.all[0]: instrument.is_electronic eq True" -> true`. Tests may be manually
set to `NOT_APPLICABLE` only with a justification of at least 10 characters.

## 6. Manual override policy

* `PASS` and `FAIL` can never be set by hand. `POST /tests/{id}/override` with
  either status returns **403** (PRD 27.2).
* Only `WAIVED` and `NOT_APPLICABLE` are acceptable override targets, each with
  a justification of at least 20 characters.
* The automated `compliance_result` is never deleted; the override is recorded in
  `manual_overrides` with the previous and new status, the previous measured
  value and the requester.
* The assigned approver/reviewer is notified.

## 7. Reproducing a result

1. `POST /tests/{id}/validate` returns the full trace without persisting:
   `intermediates`, per-row `rows`, `errors`, `warnings`, `explanation`,
   `rule_id`, `rule_version`, `clause_reference`, `rounding_policy`.
2. `POST /tests/{id}/calculate` persists a `calculation_runs` row and updates
   `compliance_results`.
3. `POST /calculations/preview` runs the engine on an ad-hoc payload for
   training or regression checks.
4. `GET /reports/{id}/snapshot` shows exactly the frozen values that were
   rendered into a released report.