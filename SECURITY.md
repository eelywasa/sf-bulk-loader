# Security Policy

## Reporting a Vulnerability

Please do not report security vulnerabilities through public GitHub issues.

If you discover a security vulnerability, please open a [GitHub Security Advisory](https://github.com/eelywasa/sf-bulk-loader/security/advisories/new) so it can be reviewed and addressed privately.

Include as much detail as possible:
- A description of the vulnerability and its potential impact
- Steps to reproduce or proof-of-concept
- Any suggested mitigations

You can expect an acknowledgement within 48 hours. Confirmed vulnerabilities will be patched and disclosed responsibly.

## Scope

This applies to the source code in this repository. Vulnerabilities in third-party dependencies should be reported directly to those projects.

## Security Considerations for Deployment

- **Encryption key** — the `ENCRYPTION_KEY` env var (Fernet key) protects stored Salesforce private keys at rest. Generate a unique key per deployment and keep it secret.
- **PostgreSQL credentials** — the default credentials in `docker-compose.postgres.yml` are for local development only. Always set a strong `POSTGRES_PASSWORD` for any non-local deployment.
- **HTTPS** — use the HTTPS overlay (`docker-compose.https.yml`) for any deployment accessible over a network.
- **Admin credentials** — set a strong `ADMIN_PASSWORD` in your `.env`. Never reuse the example value.
