# tests/e2e/sf/sfdx-devhub

SFDX project for the **Dev Hub ECA** used by E2E Tier 2 CI to authenticate as
a JWT-bearing app and create scratch orgs.

## Why this exists (separate from `tests/e2e/sf/sfdx/`)

`tests/e2e/sf/sfdx/` produces the **bulk-loader ECA** that gets deployed into
each *scratch org* by SFBL-324 — that's the app the Bulk Loader itself
authenticates against. This project here, in contrast, produces the **Dev Hub
ECA** that's deployed once into the *Dev Hub org* and used by CI to spin up
those scratch orgs in the first place.

Both ECAs use the same keypair (`~/.sfbl-e2e/server.{key,crt}` from the
SFBL-332 spike). Same public certificate registered against two distinct
ECAs in two distinct orgs.

## Why SFDX-deploy this, not UI-create

The Salesforce UI's "New External Client App" wizard creates only the bare
`ExternalClientApplication` entity — the supporting OAuth metadata
(`ExtlClntAppOauthSettings`, `ExtlClntAppGlobalOauthSettings`,
`ExtlClntAppOauthConfigurablePolicies`) lives somewhere not exposed via the
Metadata API for UI-created ECAs. Direct JWT auth works, but scratch-org
**propagation** (the Dev Hub trying to bootstrap a Connected App in the new
scratch on behalf of the requesting client) fails with C-1016
`RemoteOrgSignupFailed` because the metadata it needs to mirror is absent.

SFDX-deployed ECAs (this project + SFBL-324's bulk-loader project) write
all 5 metadata types as a complete bundle, which makes scratch-org
propagation work end-to-end.

## One-time setup

After authenticating to the Dev Hub locally (`sf org login web` or `sf org
login jwt` against the Dev Hub), run:

```bash
bash tests/e2e/sf/scripts/setup_devhub_eca.sh msjDevHub
```

The script handles deploy + permset assignment + SetupEntityAccess link +
consumer key discovery, and prints the new consumer key for pasting into
the `SFDX_DEVHUB_CONSUMER_KEY` GitHub Actions secret.

## Files

- `sfdx-project.json` — minimal SFDX project pointing at force-app/.
- `force-app/main/default/externalClientApps/SfblE2ECiDevHub.eca-meta.xml`
- `force-app/main/default/extlClntAppOauthSettings/SfblE2ECiDevHub.ecaOauth-meta.xml`
- `force-app/main/default/extlClntAppGlobalOauthSets/SfblE2ECiDevHub.ecaGlblOauth-meta.xml`
- `force-app/main/default/extlClntAppOauthPolicies/SfblE2ECiDevHub.ecaOauthPlcy-meta.xml`
- `force-app/main/default/permissionsets/SfblE2ECiDevHubPermSet.permissionset-meta.xml`

## Cleanup of the UI-created predecessor

The earlier UI-created `SFBulkLoaderDevHubCI` ECA in the Dev Hub is harmless
to leave in place (different DeveloperName), but you can remove it via:

Setup → External Client App Manager → SFBulkLoaderDevHubCI → Delete.

Doing so doesn't affect this `SfblE2ECiDevHub` ECA.
