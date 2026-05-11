# app/sfdx — App-specific SFDX metadata

Custom SObjects and fields specific to the bulk-loader E2E test scenarios.
Populated by SFBL-324. Kept separate from `sf/sfdx/` so app-specific schema changes don't pollute the shared Salesforce baseline.

## Contents

| Path | What it is |
|---|---|
| `sfdx-project.json` | SFDX project manifest (API 64.0, no namespace) |
| `force-app/…/objects/Account/fields/External_Id__c.field-meta.xml` | External ID field on Account — text(255), unique, externalId=true. Used for upsert operations in load tests. |
| `force-app/…/objects/SfblE2ETest__c/SfblE2ETest__c.object-meta.xml` | Throwaway custom object for E2E load tests — insert/upsert/delete without touching standard objects. |
| `force-app/…/objects/SfblE2ETest__c/fields/External_Id__c.field-meta.xml` | External ID field on `SfblE2ETest__c` — proves upsert loop end-to-end. |

## Deploy alongside sf/sfdx

Both SFDX projects must be deployed to a scratch org before running Tier 2 tests.
SFBL-325's `setup_scratch_org.sh` deploys `sf/sfdx/` first (ECA baseline), then `app/sfdx/` (test schema).

## Adding more test schema

Add new custom fields under `force-app/main/default/objects/<Object>/fields/`.
Add new custom objects under `force-app/main/default/objects/<Object>/`.
Keep test-only schema here; do not add it to `sf/sfdx/`.
