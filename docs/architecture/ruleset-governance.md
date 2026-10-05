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

The System Administrator can activate any populated version directly from the
Standards & rules console; completing the review lifecycle is not a prerequisite.
If an optional review has been completed, activation records that basis. Otherwise
the version is marked `super_admin_direct`. Active versions can be deactivated
directly by the System Administrator. Versions with no rule definitions remain ineligible:
they cannot generate an evaluation plan.

## The activation gate

The optional review workflow remains content-bound. A `RuleReview` carries a
fingerprint of the reviewed content, so editing a band or tolerance makes that
review stale. These records remain visible for governance and audit, but a stale
or missing review does not block a direct System Administrator activation.

The review package still reports review gaps for informational purposes. An empty
ruleset is refused at activation because it has no definitions from which to
generate tests for an evaluation.

The review package continues to describe gaps for auditing, independently of
whether the System Administrator activates the version directly:

```json
{
  "can_activate": false,
  "activation_gate": {
    "summary": "3 of 17 rules have no current metrology review",
    "unreviewed_rules": [
      {"rule_version_id": "...", "rule_code": "R76-MPE-III-VERIFICATION",
       "clause_reference": "OIML R 76-1:2006 5.2.2 / Table 1",
       "reason": "rule changed after it was reviewed"}
    ]
  }
}
```

When the optional review workflow is used, each rule sign-off is separate from
the whole-ruleset approval; direct System Administrator activation does not require either.

## Permissions

| Permission | Held by | Allows |
|---|---|---|
| `rules.view` | all roles | read standards, rules and the review package |
| `rules.manage` | System Administrator | submit a draft for optional review |
| `rules.review` | System Administrator | record an optional metrology review of a rule |
| `rules.approve` | System Administrator | optionally approve or schedule; directly activate or deactivate |

Every activation and deactivation is restricted to the System Administrator and is
recorded in the audit trail. Review metadata remains available, but the
System Administrator does not have to complete a separate review/approval workflow before
activating a populated version.

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

Seeding the catalogue creates the shipped populated version and a separate empty
placeholder for a future committee draft. On a fresh database the bootstrap
activates the shipped version as follows:

* outside production, or with `DEMO_MODE` on, it activates it **provisionally** -
  `activation_basis='provisional'`, no reviewer is invented, and the API, the
  admin console and the report footer all label it;
* in production it leaves the version in **draft**. A System Administrator can activate
  this populated version directly from Standards & rules; no review workflow is
  required.

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
