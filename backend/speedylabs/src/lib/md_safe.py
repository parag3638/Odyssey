"""Making a value safe to put inside a markdown table cell.

Two different dangers, and they need different treatment:

* **Markdown structure.** A `|` ends a table cell and a backtick opens a code span, so a
  distributor's customer name containing either would silently break the table it is in. Both
  are escaped.
* **Spreadsheets.** A checklist gets pasted into Excel at least as often as it is read in a
  terminal, so a cell beginning `=`, `+`, `-` or `@` is prefixed exactly as the canonical CSV's
  cells are - by calling `csv_safe.safe_cell`, not by a second copy of the rule, so the two
  exports cannot drift apart.

Newlines collapse to spaces: a cell cannot contain one, and `\\n` inside a table row produces a
broken table rather than a wrapped cell.
"""

from __future__ import annotations

from src.lib.csv_safe import safe_cell

#: Characters that mean something to markdown inside a table cell.
_ESCAPES = {
    "\\": "\\\\",
    "|": "\\|",
    "`": "\\`",
}


def cell(value: str) -> str:
    """One markdown table cell: spreadsheet-inert, structurally harmless."""
    text = safe_cell(" ".join(value.split()))
    return "".join(_ESCAPES.get(char, char) for char in text)


def text(value: str) -> str:
    """Prose outside a table. Newlines survive; the spreadsheet guard still applies, because a
    reader pasting a whole checklist into a sheet pastes these lines too."""
    return safe_cell(value)
