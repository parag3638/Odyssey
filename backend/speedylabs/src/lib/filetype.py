"""What kind of file this really is, decided from its bytes.

The filename extension and the client's `Content-Type` are both attacker-controlled and
both ignored. `BACKEND.md` §4: "check the real file type". A PDF renamed `export.csv`
has to be refused, and the only way to know is to look.

What we accept is narrow on purpose: the two formats the spec names, which are plain
ASCII text. Anything with a recognisable binary signature, a NUL byte, or bytes outside
the printable-ASCII-plus-whitespace range is refused. That rejects every archive,
document, image and executable without needing a signature list for each.
"""

from __future__ import annotations

from typing import Final

from src.lib.errors import UnsupportedFileType

#: Signatures worth naming in the refusal message, because they are what people
#: actually upload by mistake.
_SIGNATURES: Final[tuple[tuple[bytes, str], ...]] = (
    (b"%PDF-", "a PDF"),
    (b"PK\x03\x04", "a ZIP archive (or an .xlsx/.docx, which is a ZIP)"),
    (b"\xd0\xcf\x11\xe0", "a legacy Office document"),
    (b"\x1f\x8b", "a gzip archive"),
    (b"BZh", "a bzip2 archive"),
    (b"\x89PNG", "a PNG image"),
    (b"\xff\xd8\xff", "a JPEG image"),
    (b"GIF8", "a GIF image"),
    (b"\x7fELF", "an executable"),
    (b"SQLite format 3", "a SQLite database"),
    (b"{", "JSON"),
    (b"[", "JSON"),
    (b"<", "XML or HTML"),
)

#: Tab, newline, carriage return, form feed. Everything else below 0x20 is binary.
_ALLOWED_CONTROL: Final = frozenset(b"\t\n\r\f")


def sniff_text(head: bytes) -> str:
    """Return a sniffed type for plain text, or raise `UnsupportedFileType`.

    Only the first chunk is inspected. That is deliberate: a hostile file does not get
    to be cheap to accept and expensive to reject, and a real export declares itself in
    its first bytes.
    """
    if not head:
        raise UnsupportedFileType("That file is empty. Choose an export with data in it.")

    for signature, label in _SIGNATURES:
        if head.startswith(signature):
            raise UnsupportedFileType(
                f"That looks like {label}, not a CSV or fixed-width text export. "
                "Send the raw ERP extract."
            )

    if b"\x00" in head:
        raise UnsupportedFileType(
            "That file contains binary data, not plain text. Send the raw ERP extract "
            "as CSV or fixed-width text."
        )

    for byte in head:
        if byte < 0x20 and byte not in _ALLOWED_CONTROL:
            raise UnsupportedFileType(
                "That file contains control characters, so it is not a plain-text "
                "export. Send the raw ERP extract."
            )
        if byte > 0x7E:
            # AS/400 extracts are ASCII. A non-ASCII byte means a different encoding,
            # which would mis-align every fixed-width field - better refused than
            # silently mis-parsed.
            raise UnsupportedFileType(
                "That file is not ASCII. Fixed-width fields are counted in bytes, so a "
                "different encoding would mis-align every column. Re-export as ASCII."
            )

    return "text/plain"
