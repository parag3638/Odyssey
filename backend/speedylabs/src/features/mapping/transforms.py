"""The closed transform set: a dict of named functions, and nothing else.

Why this is a registry and not an expression language
-----------------------------------------------------
A transform is the instruction "read this source column *this way*". The obvious design
is a tiny expression language, and it is the wrong one. From P6 these instructions are
proposed by a model, and `BACKEND.md` §4 says model output is never run as code, SQL or
shell. An expression language is precisely a way to run model output as code - so there
is no evaluator here, no `eval`, no `compile`, no `getattr` by name, and no string that
ever becomes behaviour.

What exists instead: a transform arrives as data
(`{"kind": "implied_decimal", "scale": 2}`) and the only thing its `kind` can do is
select a parser from `_PARSERS`, a dict keyed by the eleven literal names in
`TRANSFORM_KINDS`. An unknown name is a `422` before anything else happens. The parser
returns a frozen dataclass, and `apply` dispatches on that dataclass with an exhaustive
`match` - so the name is used once, as a dict key, and never again. No string reaches a
call site.

The names and parameter shapes are a one-way door (mvp.md P5): they are serialised into
every stored mapping spec, so a rename invalidates saved specs that cannot be
re-derived.

Errors are per value, on purpose
--------------------------------
`apply` raises `RuleBroken` naming the offending value. It does not return a sentinel and
it does not swallow anything. The caller - a validator over 5,000 sampled rows, or the
materialiser - catches it per row and records a failed row, so one unparseable value
costs one row rather than the file. 5.3 and 5.6 both depend on that shape.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal, cast

from src.lib.errors import InvalidTransform, RuleBroken
from src.lib.parsers.quirks import MAX_SCALE, MIN_SCALE, decode_cyymmdd

#: Transform names, as they appear in a stored spec. A one-way door.
TRANSFORM_KINDS: Final = (
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
)

#: `date_format` patterns a caller may ask for. A closed list, not an arbitrary strftime
#: string: `%` formats are a small language of their own, and an unbounded one invites
#: both confusion and surprising locale behaviour. These are the shapes an ERP export
#: actually uses.
DATE_FORMATS: Final = {
    "YYYYMMDD": "%Y%m%d",
    "YYYY-MM-DD": "%Y-%m-%d",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM-DD-YYYY": "%m-%d-%Y",
    "YYYYMM": "%Y%m",
    "MMDDYY": "%m%d%y",
    "DDMMYY": "%d%m%y",
    "YYMMDD": "%y%m%d",
}

#: `multiply` factors are bounded. A factor is for unit conversion (cases to eaches, a
#: pack size), not for arbitrary arithmetic, and an unbounded one is a way to turn a
#: plausible number into an implausible one with no error anywhere.
MAX_MULTIPLY_FACTOR: Final = Decimal("1000000")

#: `map_values` tables are bounded too: a UOM or flag translation has a handful of
#: entries, and an unbounded table is unbounded stored jsonb in every spec.
MAX_MAP_ENTRIES: Final = 200
MAX_MAP_KEY_LENGTH: Final = 200


@dataclass(frozen=True)
class NoTransform:
    kind: Literal["none"] = "none"


@dataclass(frozen=True)
class Trim:
    kind: Literal["trim"] = "trim"


@dataclass(frozen=True)
class Upper:
    kind: Literal["upper"] = "upper"


@dataclass(frozen=True)
class CyymmddToDate:
    kind: Literal["cyymmdd_to_date"] = "cyymmdd_to_date"


@dataclass(frozen=True)
class StripLeadingZeros:
    kind: Literal["strip_leading_zeros"] = "strip_leading_zeros"


@dataclass(frozen=True)
class DateFormat:
    fmt: str
    kind: Literal["date_format"] = "date_format"


@dataclass(frozen=True)
class ImpliedDecimal:
    scale: int
    kind: Literal["implied_decimal"] = "implied_decimal"


@dataclass(frozen=True)
class TrailingSignDecimal:
    scale: int
    kind: Literal["trailing_sign_decimal"] = "trailing_sign_decimal"


@dataclass(frozen=True)
class MapValues:
    #: Stored as a tuple of pairs so the dataclass stays hashable and frozen; the lookup
    #: dict is built once in `__post_init__`'s stead by `table_dict`.
    pairs: tuple[tuple[str, str], ...]
    kind: Literal["map_values"] = "map_values"

    def table_dict(self) -> dict[str, str]:
        return dict(self.pairs)


@dataclass(frozen=True)
class Constant:
    value: str
    kind: Literal["constant"] = "constant"


@dataclass(frozen=True)
class Multiply:
    factor: Decimal
    kind: Literal["multiply"] = "multiply"


Transform = (
    NoTransform
    | Trim
    | Upper
    | CyymmddToDate
    | StripLeadingZeros
    | DateFormat
    | ImpliedDecimal
    | TrailingSignDecimal
    | MapValues
    | Constant
    | Multiply
)

#: What a transform may produce. Deliberately narrow: a transform's output feeds a
#: canonical field, and every canonical field is one of these.
TransformOutput = str | Decimal | date | None


# --------------------------------------------------------------------------------------
# The functions. Each takes the raw source value and its own parsed parameters.
# --------------------------------------------------------------------------------------


def _blank(value: str) -> bool:
    return value.strip() == ""


def _apply_none(value: str, _: NoTransform) -> TransformOutput:
    return value


def _apply_trim(value: str, _: Trim) -> TransformOutput:
    return value.strip()


def _apply_upper(value: str, _: Upper) -> TransformOutput:
    return value.strip().upper()


def _apply_cyymmdd(value: str, _: CyymmddToDate) -> TransformOutput:
    if _blank(value):
        return None
    try:
        return decode_cyymmdd(value)
    except Exception as error:
        raise RuleBroken(f"{value.strip()!r} is not a CYYMMDD date.") from error


def _apply_date_format(value: str, transform: DateFormat) -> TransformOutput:
    if _blank(value):
        return None
    pattern = DATE_FORMATS[transform.fmt]
    try:
        return datetime.strptime(value.strip(), pattern).date()
    except ValueError as error:
        raise RuleBroken(f"{value.strip()!r} is not a date in {transform.fmt} format.") from error


def _apply_implied_decimal(value: str, transform: ImpliedDecimal) -> TransformOutput:
    if _blank(value):
        return None
    text = value.strip()
    if not text.isdigit():
        raise RuleBroken(
            f"{text!r} is not a whole number, so it cannot be read with "
            f"{transform.scale} implied decimal places."
        )
    # `scaleb` shifts the point; it is exact and cannot round.
    return Decimal(text).scaleb(-transform.scale)


def _apply_trailing_sign(value: str, transform: TrailingSignDecimal) -> TransformOutput:
    if _blank(value):
        return None
    text = value.strip()
    sign = 1
    if text and text[-1] in "+-":
        sign = -1 if text[-1] == "-" else 1
        text = text[:-1]
    if not text.isdigit():
        raise RuleBroken(f"{value.strip()!r} is not a number with an optional trailing sign.")
    return (Decimal(sign) * Decimal(text)).scaleb(-transform.scale)


def _apply_strip_leading_zeros(value: str, _: StripLeadingZeros) -> TransformOutput:
    if _blank(value):
        # A blank field is absent, like every other transform here treats it. Returning
        # "0" would turn "this file does not record a pack size" into "the pack size is
        # zero" - a different and false statement, and one that would make the same
        # column disagree between the CSV and fixed-width renderings of one file.
        return None
    text = value.strip().lstrip("0")
    # All zeros really is the number zero, which is not the same as blank.
    return text if text else "0"


def _apply_map_values(value: str, transform: MapValues) -> TransformOutput:
    if _blank(value):
        return None
    key = value.strip()
    table = transform.table_dict()
    if key not in table:
        raise RuleBroken(
            f"{key!r} is not in this column's value map. Add it to the mapping, or "
            "correct the source data."
        )
    return table[key]


def _apply_constant(_: str, transform: Constant) -> TransformOutput:
    """Ignores the source value on purpose: a constant is for a field the file does not
    carry at all (a single-currency export, a vendor that is always the same)."""
    return transform.value


def _apply_multiply(value: str, transform: Multiply) -> TransformOutput:
    if _blank(value):
        return None
    try:
        amount = Decimal(value.strip())
    except InvalidOperation as error:
        raise RuleBroken(f"{value.strip()!r} is not a number, so it cannot be scaled.") from error
    return amount * transform.factor


def apply(transform: Transform, value: str) -> TransformOutput:
    """Apply one parsed transform to one raw source value.

    Dispatch is an exhaustive `match` over the parsed dataclasses rather than a lookup in
    a dict of functions. Both are equally closed - neither turns a string into behaviour
    - but this one the type checker can verify: adding a transform to the union without
    handling it here is a type error, where a dict would have been a `KeyError` found by
    whoever used it first.

    Raises `RuleBroken` naming the value when it cannot be read. The caller records a
    failed row and carries on - one bad value must not cost the file.
    """
    match transform:
        case NoTransform():
            return _apply_none(value, transform)
        case Trim():
            return _apply_trim(value, transform)
        case Upper():
            return _apply_upper(value, transform)
        case CyymmddToDate():
            return _apply_cyymmdd(value, transform)
        case StripLeadingZeros():
            return _apply_strip_leading_zeros(value, transform)
        case DateFormat():
            return _apply_date_format(value, transform)
        case ImpliedDecimal():
            return _apply_implied_decimal(value, transform)
        case TrailingSignDecimal():
            return _apply_trailing_sign(value, transform)
        case MapValues():
            return _apply_map_values(value, transform)
        case Constant():
            return _apply_constant(value, transform)
        case Multiply():
            return _apply_multiply(value, transform)


# --------------------------------------------------------------------------------------
# Parsing. The only way a transform enters the system.
# --------------------------------------------------------------------------------------


def parse(document: object) -> Transform:
    """Turn a wire document into a parsed transform, or raise `InvalidTransform`.

    Every failure path here is a `422`. There is deliberately no fallback to a default
    transform: silently treating an unrecognised instruction as `none` would mean a
    mapping that does something other than what it says.
    """
    if not isinstance(document, dict):
        raise InvalidTransform(
            "A transform must be an object with a 'kind'.",
            fields={"transform": "Expected an object."},
        )
    # One cast at the boundary. Narrowing `object` to `dict` with isinstance leaves
    # pyright holding `dict[Unknown, Unknown]`, which then infects every value read out
    # of it - and the keys really are strings, because this came from JSON.
    raw = cast("dict[str, Any]", document)
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in _PARSERS:
        raise InvalidTransform(
            f"{kind!r} is not a transform this tool knows.",
            fields={"transform": f"Allowed kinds: {', '.join(TRANSFORM_KINDS)}."},
        )

    known = set(_PARAMETERS[kind]) | {"kind"}
    unexpected = sorted(set(raw) - known)
    if unexpected:
        raise InvalidTransform(
            f"{kind} takes no {', '.join(unexpected)}.",
            fields={"transform": f"Unexpected: {', '.join(unexpected)}."},
        )

    return _PARSERS[kind](raw)


def _require_scale(raw: dict[str, Any]) -> int:
    scale = raw.get("scale")
    if not isinstance(scale, int) or isinstance(scale, bool):
        raise InvalidTransform(
            "This transform needs a whole-number 'scale'.",
            fields={"transform": f"scale must be an integer {MIN_SCALE}-{MAX_SCALE}."},
        )
    if not MIN_SCALE <= scale <= MAX_SCALE:
        raise InvalidTransform(
            f"An implied scale of {scale} is outside the allowed range {MIN_SCALE}-{MAX_SCALE}.",
            fields={"transform": f"scale must be {MIN_SCALE}-{MAX_SCALE}."},
        )
    return scale


def _parse_date_format(raw: dict[str, Any]) -> Transform:
    fmt = raw.get("fmt")
    if not isinstance(fmt, str) or fmt not in DATE_FORMATS:
        raise InvalidTransform(
            f"{fmt!r} is not a date format this tool knows.",
            fields={"transform": f"Allowed formats: {', '.join(DATE_FORMATS)}."},
        )
    return DateFormat(fmt=fmt)


def _parse_map_values(raw: dict[str, Any]) -> Transform:
    """Accepts the list-of-pairs wire form, and a plain object for convenience.

    Pairs are the canonical wire shape because P6's strict Structured Outputs cannot
    emit an object with dynamic keys - the model has to produce
    `[{"from": "CASE", "to": "CS"}]`. Building the converter here rather than in P6 keeps
    the whole transform vocabulary in one file, which is the point of a closed set.
    """
    table = raw.get("table")
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    entries: list[tuple[Any, Any]] = []

    if isinstance(table, dict):
        entries = list(cast("dict[str, Any]", table).items())
    elif isinstance(table, list):
        for item in cast("list[Any]", table):
            if not isinstance(item, dict):
                raise InvalidTransform(
                    "Each value-map entry must be an object with 'from' and 'to'.",
                    fields={"transform": "Expected {'from': ..., 'to': ...}."},
                )
            entry = cast("dict[str, Any]", item)
            unexpected = sorted(set(entry) - {"from", "to"})
            if unexpected:
                raise InvalidTransform(
                    f"A value-map entry takes no {', '.join(unexpected)}.",
                    fields={"transform": f"Unexpected: {', '.join(unexpected)}."},
                )
            entries.append((entry.get("from"), entry.get("to")))
    else:
        raise InvalidTransform(
            "map_values needs a 'table'.",
            fields={"transform": "Expected a list of {'from': ..., 'to': ...} entries."},
        )

    if len(entries) > MAX_MAP_ENTRIES:
        raise InvalidTransform(
            f"That value map has {len(entries):,} entries, past the limit of {MAX_MAP_ENTRIES}.",
            fields={"transform": f"At most {MAX_MAP_ENTRIES} entries."},
        )

    for source, target in entries:
        if not isinstance(source, str) or not isinstance(target, str):
            raise InvalidTransform(
                "A value map translates text to text.",
                fields={"transform": "Both 'from' and 'to' must be strings."},
            )
        if not source:
            raise InvalidTransform(
                "A value map cannot translate an empty value.",
                fields={"transform": "'from' must not be empty."},
            )
        if len(source) > MAX_MAP_KEY_LENGTH or len(target) > MAX_MAP_KEY_LENGTH:
            raise InvalidTransform(
                f"A value-map entry is longer than {MAX_MAP_KEY_LENGTH} characters.",
                fields={"transform": f"At most {MAX_MAP_KEY_LENGTH} characters."},
            )
        if source in seen:
            # Duplicates are refused rather than last-wins: two rules for one value mean
            # the author believes something untrue about their own data.
            raise InvalidTransform(
                f"{source!r} appears twice in the value map.",
                fields={"transform": f"Duplicate 'from' value: {source}."},
            )
        seen.add(source)
        pairs.append((source, target))

    if not pairs:
        raise InvalidTransform(
            "A value map needs at least one entry.",
            fields={"transform": "Expected at least one {'from': ..., 'to': ...}."},
        )
    return MapValues(pairs=tuple(pairs))


def _parse_constant(raw: dict[str, Any]) -> Transform:
    value = raw.get("value")
    if not isinstance(value, str):
        raise InvalidTransform(
            "constant needs a text 'value'.",
            fields={"transform": "value must be a string."},
        )
    if len(value) > MAX_MAP_KEY_LENGTH:
        raise InvalidTransform(
            f"A constant longer than {MAX_MAP_KEY_LENGTH} characters is not a field value.",
            fields={"transform": f"At most {MAX_MAP_KEY_LENGTH} characters."},
        )
    return Constant(value=value)


def _parse_multiply(raw: dict[str, Any]) -> Transform:
    factor = raw.get("factor")
    # A float is refused outright: a factor arriving as 0.1 is already not 0.1, and this
    # multiplies money.
    if isinstance(factor, float):
        raise InvalidTransform(
            "A multiply factor must be sent as a decimal string, not a number.",
            fields={"transform": "Send factor as a decimal string, e.g. '1.5'."},
        )
    if isinstance(factor, bool) or not isinstance(factor, str | int):
        raise InvalidTransform(
            "multiply needs a 'factor'.",
            fields={"transform": "factor must be a decimal string."},
        )
    try:
        parsed = Decimal(str(factor))
    except InvalidOperation as error:
        raise InvalidTransform(
            f"{factor!r} is not a number.",
            fields={"transform": "factor must be a decimal string."},
        ) from error
    if not parsed.is_finite():
        raise InvalidTransform(
            "A multiply factor must be a finite number.",
            fields={"transform": "factor must be finite."},
        )
    if parsed == 0:
        raise InvalidTransform(
            "A multiply factor of zero would discard the column's values.",
            fields={"transform": "factor must not be zero."},
        )
    if abs(parsed) > MAX_MULTIPLY_FACTOR:
        raise InvalidTransform(
            f"A multiply factor beyond {MAX_MULTIPLY_FACTOR:,} is not a unit conversion.",
            fields={"transform": f"factor must be at most {MAX_MULTIPLY_FACTOR:,}."},
        )
    return Multiply(factor=parsed)


#: Parameter names each kind accepts, so an unexpected one is refused by name.
_PARAMETERS: Final[Mapping[str, tuple[str, ...]]] = {
    "none": (),
    "trim": (),
    "upper": (),
    "cyymmdd_to_date": (),
    "strip_leading_zeros": (),
    "date_format": ("fmt",),
    "implied_decimal": ("scale",),
    "trailing_sign_decimal": ("scale",),
    "map_values": ("table",),
    "constant": ("value",),
    "multiply": ("factor",),
}

_PARSERS: Final[Mapping[str, Callable[[dict[str, Any]], Transform]]] = {
    "none": lambda _: NoTransform(),
    "trim": lambda _: Trim(),
    "upper": lambda _: Upper(),
    "cyymmdd_to_date": lambda _: CyymmddToDate(),
    "strip_leading_zeros": lambda _: StripLeadingZeros(),
    "date_format": _parse_date_format,
    "implied_decimal": lambda raw: ImpliedDecimal(scale=_require_scale(raw)),
    "trailing_sign_decimal": lambda raw: TrailingSignDecimal(scale=_require_scale(raw)),
    "map_values": _parse_map_values,
    "constant": _parse_constant,
    "multiply": _parse_multiply,
}


# Enforced at import rather than asserted by a test: `TRANSFORM_KINDS` is the published
# contract and stays written out so it reads as one, but a name published without a
# parser would be a 500 the first time someone used it, and a parser without a published
# name would be silently unreachable. A `RuntimeError`, not an `assert`, because `-O`
# strips asserts and this is a correctness invariant rather than a debugging aid.
if set(TRANSFORM_KINDS) != set(_PARSERS) or set(TRANSFORM_KINDS) != set(_PARAMETERS):
    raise RuntimeError(
        "transform registry is inconsistent: "
        f"published={sorted(TRANSFORM_KINDS)} parsers={sorted(_PARSERS)} "
        f"parameters={sorted(_PARAMETERS)}"
    )


def serialise(transform: Transform) -> dict[str, Any]:
    """The wire form, for storing in a spec and returning from the API.

    `map_values` goes out as pairs, which is the shape P6's model must emit and the shape
    `parse` round-trips.
    """
    if isinstance(transform, MapValues):
        return {
            "kind": "map_values",
            "table": [{"from": source, "to": target} for source, target in transform.pairs],
        }
    if isinstance(transform, DateFormat):
        return {"kind": "date_format", "fmt": transform.fmt}
    if isinstance(transform, ImpliedDecimal):
        return {"kind": "implied_decimal", "scale": transform.scale}
    if isinstance(transform, TrailingSignDecimal):
        return {"kind": "trailing_sign_decimal", "scale": transform.scale}
    if isinstance(transform, Constant):
        return {"kind": "constant", "value": transform.value}
    if isinstance(transform, Multiply):
        return {"kind": "multiply", "factor": f"{transform.factor:f}"}
    return {"kind": transform.kind}
