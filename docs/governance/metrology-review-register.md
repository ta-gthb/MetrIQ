# Metrology review register

OIML R 76-1:2006, OIML R 76-2:2007 and the limits this platform applies are
**configuration data**, not verified metrological truth. Audit item 6 of the
review checklist requires that a qualified metrology reviewer confirms every
formula, MPE boundary, applicability rule, unit conversion, rounding rule and
clause reference against the controlled standard text before the platform is
used for a verification decision.

This register is that confirmation record. It is generated from the versioned
ruleset for the clause and value columns, so it cannot describe a rule that
does not exist; the review columns are completed by the reviewer.

## 1. What the reviewer confirms

| Item | Where it lives | What must be confirmed |
| --- | --- | --- |
| Formulas | `backend/app/services/calculation_engine/calculators.py` | Each procedure's arithmetic matches the test procedure in the standard. |
| MPE bands | `backend/app/rules/data/r76-1-2006-v1.json` (`mpe`) | Band boundaries, factors, unit and the `m <= bound` resolution rule. |
| Tolerances | the same file (`tolerances`) | Factor, unit (e / d / mpe), comparator and clause reference. |
| Applicability | each catalogue entry's `applicability_expression` | The conditions select exactly the instruments the procedure applies to. |
| Observation structure | each catalogue entry's `validation_rules` | Required rows, required fields, sequence and minimum period. |
| Unit conversion | `backend/app/utils/units.py` | Mass conversions and the rounding of converted values. |
| Rounding | `backend/app/utils/decimals.py` | `ROUND_HALF_UP` at display resolution; exact comparison otherwise. |
| Boundary cases | `backend/tests/test_rule_boundaries.py` | The automated case that pins each numeric edge. |

## 2. Rules requiring confirmation

Status values: `pending` (not yet confirmed), `confirmed` (accepted as
configured), `replaced` (the reviewer supplied a different value, which is now
in a new ruleset version). A row may only be `confirmed` or `replaced` when a
named reviewer and a date are recorded.

| Rule | Clause | Configured value | Origin | Review status | Reviewer | Reviewed on |
| --- | --- | --- | --- | --- | --- | --- |
| `R76-MPE-I-VERIFICATION` | OIML R 76-1:2006 5.2.2 / Table 1 (initial verification) | 3 band(s): 0-50000 e, 50000-200000 e, 200000-inf e | project baseline | pending | - | - |
| `R76-MPE-II-VERIFICATION` | OIML R 76-1:2006 5.2.2 / Table 1 (initial verification) | 3 band(s): 0-5000 e, 5000-20000 e, 20000-inf e | project baseline | pending | - | - |
| `R76-MPE-III-VERIFICATION` | OIML R 76-1:2006 5.2.2 / Table 1 (initial verification) | 3 band(s): 0-500 e, 500-2000 e, 2000-inf e | project baseline | pending | - | - |
| `R76-MPE-IIII-VERIFICATION` | OIML R 76-1:2006 5.2.2 / Table 1 (initial verification) | 3 band(s): 0-50 e, 50-200 e, 200-inf e | project baseline | pending | - | - |
| `R76-MPE-I-IN_SERVICE` | OIML R 76-1:2006 5.2.2 / Table 1 (initial verification) | 3 band(s): 0-50000 e, 50000-200000 e, 200000-inf e | project baseline | pending | - | - |
| `R76-MPE-II-IN_SERVICE` | OIML R 76-1:2006 5.2.2 / Table 1 (initial verification) | 3 band(s): 0-5000 e, 5000-20000 e, 20000-inf e | project baseline | pending | - | - |
| `R76-MPE-III-IN_SERVICE` | OIML R 76-1:2006 5.2.2 / Table 1 (initial verification) | 3 band(s): 0-500 e, 500-2000 e, 2000-inf e | project baseline | pending | - | - |
| `R76-MPE-IIII-IN_SERVICE` | OIML R 76-1:2006 5.2.2 / Table 1 (initial verification) | 3 band(s): 0-50 e, 50-200 e, 200-inf e | project baseline | pending | - | - |
| `R76-MPE-ZERO` | OIML R 76-1:2006 4.5.5 / T.3.2.1 - REVIEW REQUIRED | 0.25 e | project baseline | pending | - | - |
| `R76-ZERO-RETURN` | OIML R 76-1:2006 4.5.4 / T.3.2.1 - REVIEW REQUIRED | factor 0.5 x e (abs_lte) | project baseline | pending | - | - |
| `R76-CREEP` | OIML R 76-1:2006 4.5.5 / T.3.3 - REVIEW REQUIRED | factor 0.5 x e (abs_lte) | project baseline | pending | - | - |
| `R76-TEMPERATURE-NO-LOAD` | OIML R 76-1:2006 4.5.2 / T.3.4.2 - REVIEW REQUIRED | factor 1.0 x e (abs_lte) | project baseline | pending | - | - |
| `R76-SENSITIVITY` | OIML R 76-1:2006 A.4.4.2 / T.3.5 - REVIEW REQUIRED | factor 1.0 x e (abs_lte) | project baseline | pending | - | - |
| `R76-DISCRIMINATION` | OIML R 76-1:2006 4.5.6 / T.3.6 - REVIEW REQUIRED | factor 1.0 x d (gte) | project baseline | pending | - | - |
| `R76-STABILITY` | OIML R 76-1:2006 4.5.3 / T.3.7 - REVIEW REQUIRED | factor 1.0 x e (abs_lte) | project baseline | pending | - | - |
| `R76-ECCENTRICITY-MPE` | OIML R 76-1:2006 T.3.5 - REVIEW REQUIRED | factor 1.0 x mpe (abs_lte) | project baseline | pending | - | - |
| `R76-TILT` | OIML R 76-1:2006 4.4.4 / T.3.6 - REVIEW REQUIRED | factor 1.0 x mpe (abs_lte) | proposal | pending | - | - |
| `R76-TARE` | OIML R 76-1:2006 4.6.4 / T.3.13 - REVIEW REQUIRED | factor 0.5 x e (abs_lte) | proposal | pending | - | - |
| `R76-WARMUP` | OIML R 76-1:2006 4.4.2 / T.3.8 - REVIEW REQUIRED | factor 1.0 x e (abs_lte) | proposal | pending | - | - |
| `R76-VOLTAGE-VARIATION` | OIML R 76-1:2006 4.4.3 / T.3.9 - REVIEW REQUIRED | factor 1.0 x e (abs_lte) | proposal | pending | - | - |
| `R76-EMC-IMMUNITY` | OIML R 76-1:2006 4.4.5 / T.3.10 - REVIEW REQUIRED | factor 1.0 x e (abs_lte) | proposal | pending | - | - |
| `R76-DAMP-HEAT` | OIML R 76-1:2006 4.4.6 / T.3.11 - REVIEW REQUIRED | factor 1.0 x mpe (abs_lte) | proposal | pending | - | - |

## 3. Sign-off

| | |
| --- | --- |
| Reviewer (name, qualification) | not yet recorded |
| Laboratory / authority | not yet recorded |
| Scope of review | every rule listed in section 2 |
| Decision | pending |
| Date | not yet recorded |

Until section 3 is completed, the platform reports every result as produced
against a ruleset whose `review_status` is `pending_domain_review`, and the
influence-factor procedures whose limits are proposals stay inactive.
