# Root composition - mirrors the CDK stack ordering in infrastructure/bin/app.ts:
#   network -> data -> (operator mirrors image + runs migration task) -> backend -> frontend
#
# Module wiring lands per child story: SFBL-380 (network), SFBL-381 (data),
# SFBL-382 (backend), SFBL-383 (frontend).

locals {
  # Every named resource shares this prefix, mirroring the CDK's
  # bulk-loader-${env} convention.
  name_prefix = "bulk-loader-${var.env_name}"

  # SES identity defaults to the hosted zone domain, like the CDK
  # (data-stack.ts: sesIdentityDomain ?? hostedZoneDomain).
  ses_identity_domain = coalesce(var.ses_identity_domain, var.hosted_zone_domain)
}
