"""
opportunity_update.py — Generate Opportunity UPDATE rows for a Salesforce UPDATE load.

Produces: opportunity_update.csv in the same directory as this script (or --out).

This script generates UPDATE payloads for Opportunity records that were seeded in a
prior INSERT step. The records must already exist in the scratch org — this script
only generates the CSV; it does not create them.

ExternalId for update: Opportunity_External_Id__c (used to match existing records)
The "ExternalIdFieldName" in the bulk-loader step config should point to this field.

Usage:
    python opportunity_update.py \\
        --prefix E2E-12345-0-0-opportunity-update- \\
        [--seed 42] [--count 25] [--out /tmp/opps.csv]

The --prefix here must match the prefix used when the Opportunity records were
originally seeded so the ExternalId values resolve correctly.

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
    fake_amount,
    fake_close_date,
    fake_stage,
    mint_ext_ids,
    seeded,
    validate_prefix,
    write_csv,
)

_DEFAULT_COUNT = 25

_OPPORTUNITY_FIELDS = [
    "Opportunity_External_Id__c",
    "StageName",
    "Amount",
    "CloseDate",
    "Description",
    "Probability",
]

_DESCRIPTIONS = [
    "Updated via E2E test fixture — Tier 2 nightly run.",
    "Bulk update test record. Do not use in production.",
    "Automated update: stage progression test.",
    "Test record: opportunity pipeline update.",
    "E2E: refreshed close date and amount.",
]


def _build_rows(prefix: str, count: int, rng_seed: int) -> list[dict]:
    """Build *count* Opportunity UPDATE rows targeting records with *prefix* ExternalIds."""
    _rng = random.Random(rng_seed)

    ext_ids = mint_ext_ids(prefix, count)
    rows: list[dict] = []
    for ext_id in ext_ids:
        stage = fake_stage()
        # Derive a plausible probability from the stage name
        probability = _stage_to_probability(stage, _rng)
        rows.append(
            {
                "Opportunity_External_Id__c": ext_id,
                "StageName": stage,
                "Amount": fake_amount(),
                "CloseDate": fake_close_date(2025),
                "Description": _rng.choice(_DESCRIPTIONS),
                "Probability": str(probability),
            }
        )
    return rows


def _stage_to_probability(stage: str, rng: random.Random) -> int:
    """Return a plausible Probability value for *stage* (0–100)."""
    stage_map = {
        "Prospecting": (10, 20),
        "Qualification": (20, 40),
        "Needs Analysis": (30, 50),
        "Value Proposition": (40, 60),
        "Id. Decision Makers": (50, 65),
        "Perception Analysis": (55, 70),
        "Proposal/Price Quote": (65, 80),
        "Negotiation/Review": (75, 90),
        "Closed Won": (100, 100),
        "Closed Lost": (0, 0),
    }
    low, high = stage_map.get(stage, (10, 90))
    return rng.randint(low, high)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Opportunity UPDATE CSV for Tier 2 E2E tests."
    )
    parser.add_argument(
        "--prefix",
        required=True,
        help=(
            "Per-test-run prefix for Opportunity ExternalIds. "
            "Must match the prefix used when the records were seeded. "
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
        help=f"Number of Opportunity rows to update (default: {_DEFAULT_COUNT}).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "opportunity_update.csv",
        help="Output CSV file path (default: opportunity_update.csv next to this script).",
    )
    args = parser.parse_args()

    # D3 validation: fail fast before writing any CSV.
    validate_prefix(args.prefix)

    with seeded(args.seed):
        rows = _build_rows(args.prefix, args.count, args.seed)

    write_csv(args.out, rows, fields=_OPPORTUNITY_FIELDS)
    print(f"Wrote {len(rows)} Opportunity update rows → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
