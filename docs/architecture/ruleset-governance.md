# Ruleset Activation

## Deployment discovery

Each versioned ruleset ships as a JSON file in `backend/app/rules/data/`. The filename stem must match its `version_label`; the payload includes `standard`, `edition`, and rule data (`mpe`, `tolerances`, or `rules`). A matching `test-catalogue-{version_label}.json` is optional.

At application startup, the loader discovers these files and seeds versions not already in the database. This incremental seed runs even when the rest of the reference catalogue is present. Existing versions and evaluation snapshots are not rewritten. New versions are listed in Administration > Standards & rules as inactive.

## Effective state

A ruleset has two operational states: `inactive` and `active`. Deployment never activates a version. Only a System Administrator can activate or deactivate it from Standards & rules.

Activating a version makes it the active version for that standard and deactivates any active sibling in the same transaction. New evaluations use the active version. Existing evaluations keep the version they were created with. Deactivation stops the version from being selected for new evaluations; an evaluation cannot explicitly select an inactive version.

There are no ruleset submission, review, approval, rejection, or scheduling stages. Historical review records and columns from earlier releases may remain in the database, but they do not appear in the active API or affect activation.

## Verification

`backend/tests/test_ruleset_governance.py` covers file discovery, inactive-by-default behavior, System Administrator authorization, activation/deactivation, and the absence of review workflow endpoints. `backend/tests/test_ops_scripts.py` exercises discovery during startup against a database that already has reference data.
