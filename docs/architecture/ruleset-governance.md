# Ruleset governance and metrology review

Audit items 6 and 10.

A rule decides whether an instrument passes verification. Software tests can show
that the rule is *implemented* consistently; they cannot show that the rule is
*correct* against the controlled OIML text. That is a human judgement, and it has
to be recorded, attributable and impossible to bypass. This document describes
how MetrIQ enforces that.

## The lifecycle

```
draft ──► under_review ──► approved ──► scheduled ──► active
  ▲            │  ▲            │            │            │
  │            ▼  │            ▼            ▼            ▼
  └──── rejected ─┘        (unschedule)  (activate)   superseded / retired
```

* **draft** - authored. Not usable for new cases.
* **under_review** - submitted for technical review.
* **approved** - a named reviewer has signed off every rule and the set as a whole.
* **scheduled** - approved, with an effective date recorded.
* **active** - the ruleset new evaluation cases are created against.
* **superseded** - it was active and a newer version of the same standard took over.
* **retired** - deliberately withdrawn.

Only `approved` and `scheduled` may become `active`. A version that is already
`active` may be recalled to `under_review` - that is the route by which a
provisionally activated ruleset gets the review it never had.

## The activation gate

`POST /api/v1/rulesets/{id}/activate` refuses unless every rule in the version
carries a **current** approval, where *current* means the fingerprint of the
reviewed content still matches the fingerprint of the stored rule. Editing a band
or a tolerance after sign-off therefore revokes that rule's approval without
anyone having to remember to clear a flag.

The refusal is specific. It answers `422` with

```json
{
  "detail": {
    "message": "This rule set cannot be activated: 3 of 17 rules have no current metrology review.",
    "rule_count": 17,
    "unreviewed_rules": [
      {"rule_version_id": "...", "rule_code": "R76-MPE-III-VERIFICATION",
       "clause_reference": "OIML R 76-1:2006 5.2.2 / Table 1",
       "reason": "rule changed after it was reviewed"}
    ]
  }
}
```

The whole rule set also needs its own approval: an approval of the individual
rules says "each band is right", the ruleset approval says "this set, together,
is the one we are putting into service".

## Permissions

| Permission | Held by | Allows |
|---|---|---|
| `rules.view` | all roles | read standards, rules and the review package |
| `rules.manage` | Super Admin | submit a draft for review |
| `rules.review` | Reviewer, Approver, Super Admin | record a metrology review of a rule |
| `rules.approve` | Approver, Super Admin | approve, reject, schedule, activate, deactivate |

Drafting, reviewing and approving are deliberately separate. A release that only
allows one person to do all three is not a controlled change process.

## The review record

Each `RuleReview` row stores:

* the reviewer's name and credentials, and the user account behind them;
* the revision of the controlled source they worked from (`source_revision`);
* the clause reference and the rule fingerprint they signed;
* the decision (`approved`, `rejected`, `needs_changes`);
* a change note;
* whether the rule's boundary cases pass, and where those cases live.

`GET /api/v1/rulesets/{id}/review-package` returns one row per rule containing
the clause, the formula, the threshold, the rounding policy and the current
review state. That is the artefact a metrology reviewer works from; it is also
what the admin console's *Review package* button downloads.

Boundary cases for every implemented rule live in
`backend/tests/test_rule_boundaries.py`, and
`test_every_catalogue_rule_has_a_boundary_case` fails if the catalogue grows a
rule that is not covered there.

## The bootstrap

Seeding the catalogue does not make it usable. On a fresh database the bootstrap
asks the lifecycle to activate the shipped version:

* outside production, or with `DEMO_MODE` on, it activates it **provisionally** -
  `activation_basis='provisional'`, no reviewer is invented, and the API, the
  admin console and the report footer all label it;
* in production it leaves the version in **draft** and logs a warning telling the
  operator to run the review workflow.

`ALLOW_PROVISIONAL_RULESET_ACTIVATION` overrides that decision explicitly.

A provisionally active ruleset keeps the platform demonstrable while making it
obvious that no metrologist has signed it off - which is the honest state for a
shipped reference catalogue.

## Historical binding

An `EvaluationCase` stores the `standard_version_id` it was created with. Nothing
in the lifecycle writes to existing cases, so activating a newer ruleset cannot
change a historical result. `test_a_case_keeps_the_ruleset_it_was_created_against`
pins this down: a case created under version A still points at A after version B
is activated and A becomes `superseded`.

## Rule diff and impact analysis

`GET /rulesets/{id}/diff` (optionally `?against=<version>`, defaulting to the
active version of the same standard) answers what a reviewer asks before
approval: which rules are added, removed or changed, field by field, and what
that touches. The impact map is derived from the test catalogue - a procedure
declares `limit_source: mpe`, `limit_source: tolerance:<key>` or a
`tolerance_key` - so it cannot drift from a hand-maintained list. The answer
also states how many reports were generated under each version and how many
cases are still open against them. A draft that does not carry its own
catalogue yet is read against the baseline's, and the response says so via
`impact.catalogue_source`. The API is read-only: nothing in it computes a
result or changes a rule.

## Audit

Every transition appends to the audit trail: `RULE_SUBMIT`, `RULE_REVIEW`,
`RULE_APPROVAL`, `RULE_REJECTION`, `RULE_SCHEDULE`, `RULE_ACTIVATION`,
`RULE_DEACTIVATION`. Each entry carries the actor, the reason and the resulting
state, so the question "who put this ruleset into production, and why" has a
single answer.

## Tests

* `backend/tests/test_ruleset_governance.py` - the lifecycle, the gate, the
  permission split, content-bound approvals, audit entries and historical
  binding.
* `backend/tests/test_rule_boundaries.py` - the numeric edges of every
  implemented rule.
* `backend/tests/test_ruleset_diff.py` - the field-level diff, the derived
  impact map and the history counts each version carries.
