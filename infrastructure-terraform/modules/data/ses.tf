# SES - domain identity for application-sent email. The app's SES backend
# sends via SES v2 SendEmail using the ECS task role; without a verified
# identity SES rejects with MailFromDomainNotVerifiedException.
#
# Two branches, mirroring data-stack.ts:
#
#   1. Fresh account (default): create the domain identity with DKIM and a
#      mail.<domain> MAIL FROM. The operator adds the DKIM CNAMEs (surfaced
#      as outputs) to DNS to complete verification.
#   2. ses_identity_adopt_existing = true: the domain identity is already
#      verified in this account (AWS refuses duplicates), so reference it by
#      constructed ARN without creating or modifying anything.

resource "aws_sesv2_email_identity" "main" {
  count = var.ses_identity_adopt_existing ? 0 : 1

  email_identity = var.ses_identity_domain
}

resource "aws_sesv2_email_identity_mail_from_attributes" "main" {
  count = var.ses_identity_adopt_existing ? 0 : 1

  email_identity   = aws_sesv2_email_identity.main[0].email_identity
  mail_from_domain = "mail.${var.ses_identity_domain}"
}

locals {
  ses_identity_arn = var.ses_identity_adopt_existing ? "arn:aws:ses:${local.region}:${local.account_id}:identity/${var.ses_identity_domain}" : aws_sesv2_email_identity.main[0].arn

  # DKIM CNAME tokens - each token T becomes a DNS record
  #   T._domainkey.<domain> CNAME T.dkim.amazonses.com
  # Empty when adopting an existing identity (DNS was configured when it was
  # first verified).
  # try(): dkim_signing_attributes is provider-computed and may be empty
  # until AWS populates it (and under test mocks).
  ses_dkim_tokens = var.ses_identity_adopt_existing ? [] : try(aws_sesv2_email_identity.main[0].dkim_signing_attributes[0].tokens, [])
}
