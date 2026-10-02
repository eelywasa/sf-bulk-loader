import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.input_connection import InputConnection
    from app.models.job import JobRecord
    from app.models.load_plan import LoadPlan


class Operation(str, enum.Enum):
    insert = "insert"
    update = "update"
    upsert = "upsert"
    delete = "delete"
    query = "query"
    queryAll = "queryAll"


class InputEncoding(str, enum.Enum):
    """Codecs an operator may select for reading a step's input CSV (SFBL-401).

    Deliberately short.  Every entry is a way for an operator to mis-set the
    encoding and write mojibake into Salesforce, so entries must earn their
    place — add more only on evidence of a real source that needs them.

    ``utf-8-sig`` is the default and reads BOM and non-BOM UTF-8 identically.
    Bare ``utf-8`` is *not* offered: Excel on Windows writes a BOM, and under
    bare ``utf-8`` that BOM survives into the first header field (``str.strip``
    does not remove U+FEFF), which would be uploaded to Salesforce as part of
    the first column name.

    ``utf-16`` is deliberately absent: with no BOM Python falls back to native
    endianness, so a UTF-16-BE file decodes cleanly into garbage — the silent
    mojibake this epic exists to remove.  Add explicit ``utf-16-le`` /
    ``utf-16-be`` if a real source ever needs them.

    Note ``latin-1`` never raises on any byte sequence, so a step set to it can
    never produce an :exc:`InputDecodeError` or a decode diagnostic.
    """

    utf_8 = "utf-8-sig"
    cp1252 = "cp1252"
    latin_1 = "latin-1"


#: Persisted/accepted encoding values, used for schema-level validation.
INPUT_ENCODINGS: frozenset[str] = frozenset(e.value for e in InputEncoding)

#: Applied when a step carries no explicit ``encoding``.
DEFAULT_INPUT_ENCODING: str = InputEncoding.utf_8.value


# Convenience sets used by validators
QUERY_OPERATIONS: frozenset[Operation] = frozenset({Operation.query, Operation.queryAll})
DML_OPERATIONS: frozenset[Operation] = frozenset({
    Operation.insert, Operation.update, Operation.upsert, Operation.delete
})


class LoadStep(Base):
    __tablename__ = "load_step"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    load_plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("load_plan.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    object_name: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[Operation] = mapped_column(
        SAEnum(Operation, name="operation_enum"), nullable=False
    )
    # Required when operation == upsert
    external_id_field: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Required for DML operations; null for query ops
    csv_file_pattern: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Required for query/queryAll operations; null for DML ops
    soql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # partition_size: None means "use the DB-backed default_partition_size setting"
    # (SFBL-156). New steps should omit this field to inherit the live default.
    partition_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    assignment_rule_id: Mapped[Optional[str]] = mapped_column(String(18), nullable=True)
    # SFBL-401: source-file encoding override.  None means "use
    # DEFAULT_INPUT_ENCODING" (UTF-8).  Deliberately a plain String, not
    # SAEnum: SAEnum creates a named type on Postgres that migrations must
    # create and drop, and schema-level validation against INPUT_ENCODINGS
    # gives the same 422 without that complexity (DECISIONS.md 032).
    encoding: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)
    # Loosely-typed source identifier: None/""/"local" → local input tree,
    # "local-output" → local output tree (SFBL-178), else an InputConnection
    # UUID.  Not a DB-level FK — resolution happens at request time in
    # app.services.input_storage.get_storage.
    input_connection_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    # SFBL-166: optional human-readable identifier; unique within a plan via
    # the partial index below (only enforced when name IS NOT NULL).
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, default=None)
    # SFBL-166: wires this step's input to an earlier query step's run-scoped
    # output. Mutually exclusive with csv_file_pattern and input_connection_id
    # (enforced at the schema/service layer).
    input_from_step_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("load_step.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )

    load_plan: Mapped["LoadPlan"] = relationship("LoadPlan", back_populates="load_steps")
    job_records: Mapped[list["JobRecord"]] = relationship("JobRecord", back_populates="load_step")

    __table_args__ = (
        Index(
            "uq_load_step_plan_name",
            "load_plan_id",
            "name",
            unique=True,
            sqlite_where=text("name IS NOT NULL"),
            postgresql_where=text("name IS NOT NULL"),
        ),
    )
