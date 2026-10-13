"""Profile response shapes."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

TypeGuess = Literal["integer", "decimal", "date", "boolean", "text", "empty"]


class PatternEntry(BaseModel):
    signature: str
    count: int


class ColumnProfileOut(BaseModel):
    column_index: int = Field(serialization_alias="columnIndex")
    column_name: str = Field(serialization_alias="columnName")
    type_guess: TypeGuess = Field(serialization_alias="typeGuess")
    row_count: int = Field(serialization_alias="rowCount")
    null_count: int = Field(serialization_alias="nullCount")
    #: A decimal string, not a float - §3 sends ratios as decimal strings so a threshold
    #: the UI colours by cannot drift between client and server.
    null_pct: str = Field(serialization_alias="nullPct")
    distinct_count: int = Field(serialization_alias="distinctCount")
    distinct_capped: bool = Field(serialization_alias="distinctCapped")
    min_text: str | None = Field(default=None, serialization_alias="minText")
    max_text: str | None = Field(default=None, serialization_alias="maxText")
    date_format: str | None = Field(default=None, serialization_alias="dateFormat")
    sample_values: list[str] = Field(serialization_alias="sampleValues")
    patterns: list[PatternEntry]
    #: Exact per-value counts, for a column with few enough distinct values to be an enumeration.
    #: Null means either "too many to be one" or "profiled before this was recorded", and
    #: `distinctCount` is what tells those apart - P10's drift checks rely on the difference.
    value_counts: dict[str, int] | None = Field(default=None, serialization_alias="valueCounts")
    #: How many values parsed as a finite number, and their exact sum as a decimal string. The mean
    #: is deliberately not sent: a reader dividing by the count can see what it came from.
    numeric_count: int | None = Field(default=None, serialization_alias="numericCount")
    numeric_sum: str | None = Field(default=None, serialization_alias="numericSum")

    model_config = {"populate_by_name": True}


class ProfileOut(BaseModel):
    source_file_id: UUID = Field(serialization_alias="sourceFileId")
    layout_version: int = Field(serialization_alias="layoutVersion")
    row_count: int = Field(serialization_alias="rowCount")
    columns: list[ColumnProfileOut]

    model_config = {"populate_by_name": True}


class ProfileEnvelope(BaseModel):
    data: ProfileOut


def column_from_row(row: dict[str, Any]) -> ColumnProfileOut:
    return ColumnProfileOut(
        column_index=row["column_index"],
        column_name=row["column_name"],
        type_guess=row["type_guess"],
        row_count=row["row_count"],
        null_count=row["null_count"],
        null_pct=f"{row['null_pct']:f}",
        distinct_count=row["distinct_count"],
        distinct_capped=row["distinct_capped"],
        min_text=row["min_text"],
        max_text=row["max_text"],
        date_format=row["date_format"],
        sample_values=list(row["sample_values"]),
        patterns=[PatternEntry(**entry) for entry in row["patterns"]],
        # `.get`, so a profile row written before migration 0019 serialises with these null rather
        # than 500ing. That is the honest answer for it, and it is the answer P10's checks read.
        value_counts=row.get("value_counts"),
        numeric_count=row.get("numeric_count"),
        numeric_sum=(None if row.get("numeric_sum") is None else f"{row['numeric_sum']:f}"),
    )
