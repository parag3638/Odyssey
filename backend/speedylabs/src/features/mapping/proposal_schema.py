"""The wire shape the model is asked to produce, under strict Structured Outputs.

Strict mode is not a formality; it changed the design. The SDK forces **every** property
into `required` and sets `additionalProperties: false` recursively, which has two
consequences that are visible all the way through this phase:

* **No dynamic keys.** A mapping keyed by canonical field name - the obvious shape, and
  the shape we store - cannot be expressed, because `additionalProperties: false` forbids
  properties the schema does not name. So the model returns a **list** of fields and the
  conversion to a keyed object happens at the service boundary.
* **Optional means nullable, not absent.** `int | None` renders as an `anyOf` with a
  `"null"` branch *and is still listed in `required`*. So `Transform` carries every
  parameter slot on every instance, with the unused ones explicitly `null`. A transform
  with six slots of which one is filled is not elegant, but it is what passes - and the
  boundary converter turns it back into the tidy closed-set document that
  `features/mapping/transforms.py` already understands.

`value_map` is a list of `{from, to}` pairs for the same reason the top level is a list.
P5's registry already accepts that wire form, which is why it was built there rather than
here: the transform vocabulary lives in one file.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field, WithJsonSchema

#: The closed set, as an enum the model must choose from. Taken from the registry's own
#: published tuple so the two cannot drift - a kind the model could name but the registry
#: could not parse would be a guaranteed dropped field.
TransformKind = Literal[
    "none",
    "trim",
    "upper",
    "cyymmdd_to_date",
    "date_format",
    "implied_decimal",
    "trailing_sign_decimal",
    "strip_leading_zeros",
    "map_values",
    "constant",
    "multiply",
]

#: Date formats the registry knows, as an enum for the same reason.
DateFormatName = Literal[
    "YYYYMMDD",
    "YYYY-MM-DD",
    "MM/DD/YYYY",
    "DD/MM/YYYY",
    "MM-DD-YYYY",
    "YYYYMM",
    "MMDDYY",
    "DDMMYY",
    "YYMMDD",
]


class ValuePair(BaseModel):
    """One translation in a `map_values` table.

    `from_value`/`to_value` rather than `from`/`to` because `from` is a Python keyword;
    the aliases put the short names on the wire, which is what the prompt asks for.
    """

    from_value: str = Field(alias="from", description="The value as the source file writes it.")
    to_value: str = Field(alias="to", description="The canonical value it means.")

    model_config = {"populate_by_name": True, "extra": "forbid"}


class ProposedTransform(BaseModel):
    """How to read the column. Every parameter slot is present; unused ones are null.

    That is strict mode's doing, not a preference - see the module docstring.
    """

    kind: TransformKind = Field(description="Which transform to apply.")
    scale: int | None = Field(
        description="Implied decimal places, 0-6. Only for implied_decimal and "
        "trailing_sign_decimal; null otherwise."
    )
    date_format: DateFormatName | None = Field(
        description="Which written date format the column uses. Only for date_format; "
        "null otherwise."
    )
    value_map: list[ValuePair] | None = Field(
        description="Translations from source values to canonical ones. Only for "
        "map_values; null otherwise."
    )
    factor: str | None = Field(
        description="A decimal string to multiply by, e.g. '1.5'. Only for multiply; "
        "null otherwise. Never a JSON number."
    )
    constant: str | None = Field(
        description="A fixed value to use for every row, for a field the file does not "
        "carry. Only for constant; null otherwise."
    )

    model_config = {"extra": "forbid"}


class ProposedField(BaseModel):
    canonical_field: str = Field(
        description="The canonical field name this source column feeds, exactly as the "
        "canonical schema spells it."
    )
    source_column: str = Field(
        description="The source column name, exactly as it appears in the profile given "
        "to you. Never invent a column."
    )
    transform: ProposedTransform
    #: A JSON *number* on the wire - which is fine, it is not money - but a `Decimal` in
    #: Python, so `src/` stays free of floats (step 0.5's guard) and no arithmetic on a
    #: confidence ever touches binary floating point. `WithJsonSchema` pins the wire type
    #: to `number`; Pydantic's default for `Decimal` is `anyOf: [number, string]`, which
    #: would invite the model to send a string.
    confidence: Annotated[
        Decimal,
        WithJsonSchema(
            {
                "type": "number",
                "description": "How confident you are, 0 to 1. Be honest: a low number "
                "on a field you had to guess is more useful than a high one everywhere.",
            }
        ),
    ]
    reason: str = Field(
        description="One sentence on what in the *sample values* led you here. Cite the "
        "evidence, not the column name."
    )

    model_config = {"extra": "forbid"}


class MappingProposal(BaseModel):
    """The top level. A list, because strict mode cannot express a keyed object."""

    fields: list[ProposedField] = Field(
        description="One entry per canonical field you can map. Omit a canonical field "
        "entirely rather than guessing wildly at it."
    )
    #: Asked for explicitly: the decoy pair in this file's data is genuinely ambiguous, so
    #: a model that says so is more useful than one that picks and sounds sure.
    ambiguities: list[str] = Field(
        description="Any canonical field where two or more source columns looked equally "
        "plausible, named as 'canonical_field: COL_A vs COL_B'. Empty list if none."
    )

    model_config = {"extra": "forbid"}


#: Bumping this changes our own cache key, so a prompt edit cannot serve stale proposals.
PROMPT_TEMPLATE_VERSION: Final = "1"
