"""
accounts_insert.py — Generate 50 Account rows for a Salesforce INSERT load.

Produces: accounts_insert.csv in the same directory as this script (or --out).

ExternalId field: External_Id__c  (custom field deployed via the SFDX project)
Usage:
    python accounts_insert.py --prefix E2E-12345-0-0-account-insert- [--seed 42] [--out /tmp/accounts.csv]

Prefix rules (D3 — hard-locked):
    - Must not contain SOQL LIKE wildcard characters (_ or %).
    - The validator in csv_factory.py will exit non-zero with a clear error
      message if the rule is violated — no CSV is written.

The script is deterministic given the same --seed: invoke it twice with
identical arguments and diff the outputs — expect an empty diff.
"""

import argparse
import sys
from pathlib import Path

# Allow running from any directory by injecting the shared sf fixtures path.
_CSV_FACTORY_DIR = Path(__file__).resolve().parents[3] / "sf" / "fixtures" / "csv"
sys.path.insert(0, str(_CSV_FACTORY_DIR))

from csv_factory import (  # noqa: E402
    fake_company,
    fake_phone,
    mint_ext_ids,
    seeded,
    validate_prefix,
    write_csv,
)

_DEFAULT_COUNT = 50
_ACCOUNT_FIELDS = [
    "External_Id__c",
    "Name",
    "Phone",
    "BillingCity",
    "BillingCountry",
    "Type",
    "Industry",
]

_ACCOUNT_TYPES = [
    "Prospect",
    "Customer - Direct",
    "Customer - Channel",
    "Channel Partner / Reseller",
    "Installation Partner",
    "Technology Partner",
    "Other",
]

_INDUSTRIES = [
    "Agriculture",
    "Apparel",
    "Banking",
    "Chemicals",
    "Communications",
    "Construction",
    "Consulting",
    "Education",
    "Electronics",
    "Energy",
    "Engineering",
    "Entertainment",
    "Environmental",
    "Finance",
    "Food & Beverage",
    "Government",
    "Healthcare",
    "Hospitality",
    "Insurance",
    "Machinery",
    "Manufacturing",
    "Media",
    "Not For Profit",
    "Recreation",
    "Retail",
    "Shipping",
    "Technology",
    "Telecommunications",
    "Transportation",
    "Utilities",
]

_CITIES = [
    "San Francisco",
    "New York",
    "Chicago",
    "Austin",
    "Seattle",
    "Boston",
    "Denver",
    "Atlanta",
    "Dallas",
    "Los Angeles",
]


def _build_rows(prefix: str, count: int, rng_seed: int) -> list[dict]:
    """Build *count* Account rows under *prefix* using *rng_seed* for determinism."""
    import random
    _rng = random.Random(rng_seed)

    ext_ids = mint_ext_ids(prefix, count)
    rows: list[dict] = []
    for ext_id in ext_ids:
        rows.append(
            {
                "External_Id__c": ext_id,
                "Name": fake_company(),
                "Phone": fake_phone(),
                "BillingCity": _rng.choice(_CITIES),
                "BillingCountry": "United States",
                "Type": _rng.choice(_ACCOUNT_TYPES),
                "Industry": _rng.choice(_INDUSTRIES),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Account INSERT CSV for Tier 2 E2E tests."
    )
    parser.add_argument(
        "--prefix",
        required=True,
        help=(
            "Per-test-run prefix for ExternalIds. "
            "Must not contain SOQL LIKE wildcard characters (_ or %%). "
            "Expected shape: E2E-{RUN_ID}-{WORKER}-{RETRY}-{TEST_SLUG}-"
        ),
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
        help=f"Number of Account rows to generate (default: {_DEFAULT_COUNT}).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "accounts_insert.csv",
        help="Output CSV file path (default: accounts_insert.csv next to this script).",
    )
    args = parser.parse_args()

    # D3 validation: fail fast before writing any CSV.
    validate_prefix(args.prefix)

    with seeded(args.seed):
        rows = _build_rows(args.prefix, args.count, args.seed)

    write_csv(args.out, rows, fields=_ACCOUNT_FIELDS)
    print(f"Wrote {len(rows)} Account rows → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
