# sf/sfdx — Shared SFDX project

SFDX project for Salesforce-shaped baseline metadata: ECA definition, JWT certificate, and baseline permission set.
Populated by SFBL-324; shape confirmed via SFBL-332 spike (`SPIKE_REPORT.md`).

## Contents

| Path | What it is |
|---|---|
| `sfdx-project.json` | SFDX project manifest (API 64.0, no namespace) |
| `config/project-scratch-def.json` | Default scratch-org definition — Developer edition + `EnableSetPasswordInApi` + `enableConsumerSecretApiAccess` |
| `config/project-scratch-def.shaped.json` | Org Shape opt-in variant; `sourceOrg` must be set to a real org ID before use (SFBL-325) |
| `force-app/…/externalClientApps/SfblE2E.eca-meta.xml` | ECA header — `distributionState: Local` |
| `force-app/…/extlClntAppOauthSettings/SfblE2E.ecaOauth-meta.xml` | OAuth scopes (Api, Web, RefreshToken, OpenID) |
| `force-app/…/extlClntAppGlobalOauthSets/SfblE2E.ecaGlblOauth-meta.xml` | Public certificate (safe to commit); generates consumer key post-deploy |
| `force-app/…/extlClntAppOauthPolicies/SfblE2E.ecaOauthPlcy-meta.xml` | `AdminApprovedPreAuthorized` policy — required for CI JWT bearer flow |
| `force-app/…/permissionsets/SfblE2EPermSet.permissionset-meta.xml` | Permission set; assign post-deploy via `sf org assign permset --name SfblE2EPermSet` |
| `SPIKE_REPORT.md` | SFBL-332 spike findings — why these shapes were chosen, surprises vs spec sketch |

## Certificate

The public certificate in `SfblE2E.ecaGlblOauth-meta.xml` is safe to commit.
The matching **private key** lives in GitHub secret `SFBL_E2E_BULK_LOADER_JWT_KEY` and is NEVER committed.
The keypair was generated during the SFBL-332 spike; certificate expires 2028-05-10.

## Post-deploy steps (managed by SFBL-325 scripts)

1. `sf org assign permset --name SfblE2EPermSet`
2. Create `SetupEntityAccess` link — see the TODO comment in `SfblE2EPermSet.permissionset-meta.xml` and `SPIKE_REPORT.md`
3. Run `discover_eca_consumer_key.sh` to retrieve the `consumerKey` from the deployed ECA
