"""Where uploaded files live, behind a seam.

`BACKEND.md` §4 wants uploads in object storage with a random stored name, never
executed. Phase 2 writes to a local directory; P17 swaps in S3-compatible storage
without touching a caller, because every caller depends on this protocol rather than on
a filesystem path.

Three rules the local implementation enforces, each closing a specific attack:

- the stored name is a **UUIDv7 with no extension**, so a client cannot choose where its
  bytes land or what they are called;
- the original filename is kept as a display string and **never used to build a path**,
  so `../../etc/passwd` is just an odd-looking label;
- files are written `0600` inside `UPLOAD_DIR` and nothing is ever marked executable.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from src.lib.errors import TooLarge
from src.lib.ids import uuid7

#: Read in 64 KB chunks: large enough that syscall overhead is irrelevant, small enough
#: that the size cap is enforced long before a hostile upload fills memory.
CHUNK_BYTES: Final = 64 * 1024


@dataclass(frozen=True)
class StoredFile:
    """What a caller needs after a successful write."""

    stored_name: str
    byte_size: int
    sha256: str
    #: The first chunk, kept so the caller can sniff the type without re-reading.
    head: bytes


class Storage(Protocol):
    """The seam. P17 replaces the implementation, not the interface."""

    async def save(self, chunks: AsyncIterator[bytes], *, max_bytes: int) -> StoredFile: ...

    def open_path(self, stored_name: str) -> Path: ...

    def delete(self, stored_name: str) -> None: ...


class LocalStorage:
    """Files on a local volume. Compose mounts it; P17 replaces it."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def open_path(self, stored_name: str) -> Path:
        # Rebuilt from the stored name, which we generated, so nothing a client sent
        # can influence the path. Guarded anyway: a bug upstream must not become a
        # traversal.
        if "/" in stored_name or "\\" in stored_name or stored_name in ("", ".", ".."):
            raise ValueError(f"refusing a suspicious stored name: {stored_name!r}")
        return self._root / stored_name

    def delete(self, stored_name: str) -> None:
        self.open_path(stored_name).unlink(missing_ok=True)

    async def save(self, chunks: AsyncIterator[bytes], *, max_bytes: int) -> StoredFile:
        """Stream to disk, hashing as we go, refusing at the cap mid-stream.

        The cap is checked **while** writing rather than after: buffering a 10 GB upload
        to discover it is too large is the denial of service the cap exists to prevent.
        A partial file is removed before the error propagates, so a rejected upload
        leaves nothing behind.
        """
        stored_name = uuid7().hex
        path = self.open_path(stored_name)
        digest = hashlib.sha256()
        total = 0
        head = b""

        # 0600 from creation, not chmod'ed afterwards - there is no window where the
        # file is group- or world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise TooLarge(
                            f"That file is larger than the {max_bytes // (1024 * 1024)} MB "
                            "limit. Split the export and upload it in parts."
                        )
                    if len(head) < CHUNK_BYTES:
                        head += chunk[: CHUNK_BYTES - len(head)]
                    digest.update(chunk)
                    handle.write(chunk)
        except BaseException:
            # Includes the cap breach above and a client that disconnects mid-upload.
            path.unlink(missing_ok=True)
            raise

        if total == 0:
            path.unlink(missing_ok=True)
            raise TooLarge("That file is empty. Choose an export with data in it.")

        return StoredFile(
            stored_name=stored_name, byte_size=total, sha256=digest.hexdigest(), head=head
        )
