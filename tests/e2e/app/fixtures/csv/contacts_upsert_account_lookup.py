"""
contacts_upsert_account_lookup.py — Generate 200 Contact rows for a Salesforce UPSERT load
                                    with an Account parent lookup.

Produces: contacts_upsert_account_lookup.csv in the same directory as this script (or --out).

Parent relationship format (H1 spec — hard-locked):
    Account__r.External_Id__c  (non-polymorphic, dot-separator)
    NOT the polymorphic colon form (Account__r:External_Id__c) — per H1 finding
    in SFBL-301, the non-poly dot-separator is the correct byte shape for the
    Bulk API 2.0 relationship-header.

ExternalId for upsert: Contact_External_Id__c

Usage:
    python contacts_upsert_account_lookup.py \\
        --prefix E2E-12345-0-0-contact-upsert- \\
        --account-prefix E2E-12345-0-0-account-insert- \\
        --account-count 50 \\
        [--seed 42] [--out /tmp/contacts.csv]

Note: --account-prefix must be the SAME prefix used when generating accounts,
and --account-count must match the account count so the lookup references are valid.

Prefix rules (D3 — hard-locked):
    - Must not contain SOQL LIKE wildcard characters (_ or %).
    - The validator in csv_factory.py will exit non-zero with a clear error
      message if the rule is violated — no CSV is written.
"""

import argparse
import random
import sys
from pathlib import Path

# Allow running from any directory by injecting the shared sf fixtures path.
_CSV_FACTORY_DIR = Path(__file__).resolve().parents[3] / "sf" / "fixtures" / "csv"
sys.path.insert(0, str(_CSV_FACTORY_DIR))

from csv_factory import (  # noqa: E402
    fake_email,
    fake_first_name,
    fake_last_name,
    fake_phone,
    fake_title,
    mint_ext_ids,
    seeded,
    validate_prefix,
    write_csv,
)

_DEFAULT_COUNT = 200
_DEFAULT_ACCOUNT_COUNT = 50

# H1 (hard-locked): non-polymorphic dot-separator form.
_ACCOUNT_LOOKUP_FIELD = "Account__r.External_Id__c"

_CONTACT_FIELDS = [
    "Contact_External_Id__c",
    "FirstName",
    "LastName",
    "Email",
    "Title",
    "Phone",
    _ACCOUNT_LOOKUP_FIELD,
    "LeadSource",
]

_LEAD_SOURCES = [
    "Web",
    "Phone Inquiry",
    "Partner Referral",
    "Purchased List",
    "Other",
    "Cold Call",
    "Internal",
    "Employee Referral",
    "Word of mouth",
]


def _build_rows(
    prefix: str,
    count: int,
    account_ext_ids: list[str],
    rng_seed: int,
) -> list[dict]:
    """Build *count* Contact rows referencing random accounts from *account_ext_ids*."""
    _rng = random.Random(rng_seed)

    contact_ext_ids = mint_ext_ids(prefix, count)
    rows: list[dict] = []
    for ext_id in contact_ext_ids:
        first = fake_first_name()
        last = fake_last_name()
        parent_ext_id = _rng.choice(account_ext_ids)
        rows.append(
            {
                "Contact_External_Id__c": ext_id,
                "FirstName": first,
                "LastName": last,
                "Email": fake_email(first, last),
                "Title": fake_title(),
                "Phone": fake_phone(),
                _ACCOUNT_LOOKUP_FIELD: parent_ext_id,
                "LeadSource": _rng.choice(_LEAD_SOURCES),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Contact UPSERT CSV with Account parent lookup for Tier 2 E2E tests."
    )
    parser.add_argument(
        "--prefix",
        required=True,
        help=(
            "Per-test-run prefix for Contact ExternalIds. "
            "Must not contain SOQL LIKE wildcard characters (_ or %%). "
            "Expected shape: E2E-{RUN_ID}-{WORKER}-{RETRY}-{TEST_SLUG}-"
        ),
    )
    parser.add_argument(
        "--account-prefix",
        required=True,
        dest="account_prefix",
        help=(
            "Prefix used when generating the parent Account rows. "
            "Must match the --prefix used in accounts_insert.py."
        ),
    )
    parser.add_argument(
        "--account-count",
        type=int,
        default=_DEFAULT_ACCOUNT_COUNT,
        dest="account_count",
        help=f"Number of Account rows that were generated (default: {_DEFAULT_ACCOUNT_COUNT}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic output (default: 42).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=_DEFAULT_COUNT,
        help=f"Number of Contact rows to generate (default: {_DEFAULT_COUNT}).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "contacts_upsert_account_lookup.csv",
        help="Output CSV file path (default: contacts_upsert_account_lookup.csv next to this script).",
    )
    args = parser.parse_args()

    # D3 validation: fail fast before writing any CSV.
    validate_prefix(args.prefix)
    validate_prefix(args.account_prefix)

    # Derive the account ExternalIds that were generated for the parent wave.
    # These are deterministic (prefix + sequential numbering) so no seed needed.
    account_ext_ids = mint_ext_ids(args.account_prefix, args.account_count)

    with seeded(args.seed):
        rows = _build_rows(args.prefix, args.count, account_ext_ids, args.seed)

    write_csv(args.out, rows, fields=_CONTACT_FIELDS)
    print(f"Wrote {len(rows)} Contact rows → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
