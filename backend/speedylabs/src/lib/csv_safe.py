"""Making a cell safe to open in a spreadsheet.

Excel, LibreOffice and Google Sheets all treat a cell beginning `=`, `+`, `-` or `@` as a
formula. A distributor's export is untrusted input, and the canonical CSV is a file a
person downloads and double-clicks - so a `customer_name` of
`=HYPERLINK("http://evil","click")` would become a live link in their spreadsheet, and
worse things are possible with `=cmd|...`.

The fix is the conventional one: prefix such a cell with a single quote, which every
spreadsheet reads as "this is text". It changes the bytes, which is why it is applied at
*export* and never at storage - the stored value stays exactly what the file said, and
`FORMAT.md` documents that the exported form may carry the prefix.

Tab and carriage return are also guarded: a leading tab is enough to trigger formula
interpretation in some versions, and a bare CR can split a row in a naive reader.
"""

from __future__ import annotations

from typing import Final

#: Characters that make a spreadsheet treat the rest of the cell as an expression.
DANGEROUS_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")

#: What marks a cell as literal text in every major spreadsheet.
TEXT_PREFIX: Final = "'"


def safe_cell(value: str) -> str:
    """Return `value` as a cell that cannot be interpreted as a formula.

    Only the *leading* character matters, so `A=B` is untouched - escaping it would
    corrupt ordinary data for no gain. A negative number like `-12.50` does get the
    prefix, which is the one place this is visibly inconvenient; it is the right trade,
    because there is no way to tell `-12.50` from `-1+cmd` without parsing, and a credit
    note reading `'-269.19` is legible while a formula executing is not.
    """
    if value and value.startswith(DANGEROUS_PREFIXES):
        return TEXT_PREFIX + value
    return value
