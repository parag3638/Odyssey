"""The attributed-variance export, in a shape a root-cause tool could be fed.

The spec asks to "export results in a format that could feed Glass". We do not know Glass's real
interface and cannot pretend to, so the honest version is a **documented, versioned, stable JSON**
of attributed variances with their source lines: a plausible shape a real integration could be
adapted to, published as a committed JSON Schema so that adaptation is a mapping exercise rather
than reverse engineering.

**It is a proposed shape, not a contract anyone has agreed to**, and `GLASS_EXPORT.md` and the
README say so. Claiming otherwise would be the one piece of dishonesty in this project.

JSON rather than CSV because a variance carries a nested list of source lines, and that nesting is
precisely the thing a downstream tool could not recompute for itself - it is the whole value of the
export. Every amount is a decimal string, for the reason every amount in this system is.
"""

from __future__ import annotations

from typing import Any, Final

#: Bumped when the shape changes. Consumers pin it; `GLASS_EXPORT.md` records what moved.
GLASS_EXPORT_VERSION: Final = "1.0.0"

#: Source lines per variance. A cap with a flag and a real count beside it, so a truncated export
#: says so rather than quietly under-reporting - the `brandsTruncated` pattern.
MAX_GLASS_SOURCE_LINES: Final = 500


def schema_document() -> dict[str, Any]:
    """The published JSON Schema. A round-trip test keeps it from drifting from the builder."""
    money = {"type": "string", "pattern": r"^-?\d+\.\d{2}$"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://speedy.example/schemas/glass_variance_export.schema.json",
        "title": "Attributed variance export",
        "description": (
            "A proposed shape for feeding attributed rebate variances into a downstream "
            "root-cause tool. Not a contract agreed with any such tool."
        ),
        "type": "object",
        "required": [
            "glassExportVersion",
            "customerName",
            "ingestRunId",
            "engine",
            "explainedPct",
            "totalVarianceAmount",
            "netVarianceAmount",
            "unexplainedAmount",
            "variances",
        ],
        "additionalProperties": False,
        "properties": {
            "glassExportVersion": {"type": "string"},
            "customerName": {"type": "string"},
            "ingestRunId": {"type": "string"},
            "legacySourceFile": {"type": ["string", "null"]},
            "periodStart": {"type": ["string", "null"], "format": "date"},
            "periodEnd": {"type": ["string", "null"], "format": "date"},
            "canonicalSchemaVersion": {"type": "string"},
            "engine": {
                "type": "object",
                "required": ["name", "version", "standIn"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                    "standIn": {"type": "boolean"},
                },
            },
            "explainedPct": {"type": ["string", "null"]},
            "totalVarianceAmount": money,
            "netVarianceAmount": money,
            "unexplainedAmount": money,
            "currency": {"type": "string"},
            "variances": {"type": "array", "items": _variance_schema(money)},
        },
    }


def _variance_schema(money: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "programExternalRef",
            "programName",
            "periodStart",
            "periodEnd",
            "legacyAmount",
            "calculatedAmount",
            "varianceAmount",
            "rootCause",
            "byCause",
            "sourceLines",
            "sourceLineCount",
            "sourceLinesTruncated",
        ],
        "additionalProperties": False,
        "properties": {
            "programExternalRef": {"type": ["string", "null"]},
            "programName": {"type": "string"},
            "periodStart": {"type": "string", "format": "date"},
            "periodEnd": {"type": "string", "format": "date"},
            "legacyAmount": money,
            "calculatedAmount": money,
            "varianceAmount": money,
            "presence": {"type": "string"},
            "rootCause": {"type": "string"},
            "byCause": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["rootCause", "amount"],
                    "additionalProperties": False,
                    "properties": {"rootCause": {"type": "string"}, "amount": money},
                },
            },
            "residueAmount": money,
            "sourceLineCount": {"type": "integer", "minimum": 0},
            "sourceLinesTruncated": {"type": "boolean"},
            "sourceLines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["rootCause", "contributionAmount"],
                    "additionalProperties": False,
                    "properties": {
                        "invoiceNo": {"type": ["string", "null"]},
                        "invoiceDate": {"type": ["string", "null"], "format": "date"},
                        "distItemId": {"type": ["string", "null"]},
                        "sourceRowNumber": {"type": ["integer", "null"]},
                        "rootCause": {"type": "string"},
                        "contributionAmount": money,
                        "evidence": {"type": ["string", "null"]},
                        "estimated": {"type": "boolean"},
                    },
                },
            },
        },
    }
