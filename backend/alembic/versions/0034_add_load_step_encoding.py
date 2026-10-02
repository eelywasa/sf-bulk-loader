"""Add load_step.encoding — per-step input encoding override (SFBL-401).

Part of SFBL-400. Input CSVs are decoded as UTF-8 (``utf-8-sig``) unless a
step carries an explicit ``encoding``; the prefix-sampling auto-detection that
shipped with the original spec §4.3 is removed in the same change.

Column notes
------------
- **Nullable, no default.** ``NULL`` means "use the UTF-8 default", so every
  existing step inherits the new behaviour without a data migration.
- **Plain ``String(16)``, not an enum type.** The ``operation`` column uses
  ``SAEnum(..., name="operation_enum")``, which creates a named type on
  Postgres that migrations must create and drop. Schema-level validation
  against ``INPUT_ENCODINGS`` gives the same 422 without that complexity
  (DECISIONS.md 032).

This is a **breaking change** for data, not for schema: a cp1252 source on a
step with no explicit encoding loads today and will fail after this ships
until an operator sets the dropdown. That is deliberate — some of those loads
are already silently corrupting data. Deliberately **no backfill**: detecting
an encoding at migration time would bake the guess into stored config, which
is harder to notice and undo than the runtime guess being removed.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "load_step",
        sa.Column("encoding", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("load_step", "encoding")
