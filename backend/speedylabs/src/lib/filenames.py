"""Download file names, slugged once.

A customer name reaches a `Content-Disposition` header, and a value containing a quote, a
newline or a non-ASCII byte is a header-injection problem rather than a formatting one. So the
name is **slugged**, not escaped: anything outside `[A-Za-z0-9-]` becomes a dash.

Here rather than in `canonical/export.py` because P8's checklist is the second download whose
name carries a customer and a run id, and the two must slug identically or the same customer
appears under two spellings in one folder (BACKEND.md §9's rule of two, at the second user).
"""

from __future__ import annotations

#: Longest slug kept from a customer name. Long enough to recognise, short enough that the
#: whole file name stays readable next to a UUID.
MAX_SLUG_LENGTH = 48


def slug(value: str, *, fallback: str = "customer") -> str:
    """ASCII, lower case, dash-separated. Never empty."""
    reduced = "".join(char if char.isascii() and char.isalnum() else "-" for char in value)
    parts = [part for part in reduced.split("-") if part]
    return "-".join(parts).lower()[:MAX_SLUG_LENGTH] or fallback


def download_name(prefix: str, customer_name: str, run_id: object, extension: str) -> str:
    """`<prefix>_<customer-slug>_<runid>.<extension>`, or the dashed form for the checklist.

    The separator is part of each artefact's published name - the canonical CSV uses
    underscores and P8's checklist uses dashes, because mvp.md fixes
    `data-readiness-checklist-<customer>-<runid>.md` verbatim and a customer's IT team may
    well have a rule that matches it. So the caller passes the whole prefix and gets the
    customer and run joined with whatever `prefix` ends in.
    """
    separator = "-" if prefix.endswith("-") else "_"
    stem = prefix.rstrip("-_")
    return f"{stem}{separator}{slug(customer_name)}{separator}{run_id}.{extension}"
