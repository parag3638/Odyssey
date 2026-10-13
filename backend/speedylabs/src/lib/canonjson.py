"""Canonical JSON: one byte sequence per document, so a hash means content.

Two things in this project depend on a document hashing the same way every time it is
written: a mapping spec's `spec_sha256`, which decides whether two specs are the same
mapping, and a draft's `validated_sha256`, which decides whether stored verdicts still
describe the mapping in front of them. Both are wrong if serialisation has any freedom.

The rules, and why each is needed:

* **Sorted keys at every level.** A JSON object's key order is not meaningful, so two
  equal mappings must not hash differently because one was built by patching fields in a
  different order.
* **Two-space indent and a trailing newline.** The document is designed to be read in a
  `git diff`, which is also why no timestamp ever goes inside it - a timestamp would make
  every diff show a change.
* **`map_values` tables are sorted by `from`.** This is the rule sorting keys does *not*
  give you: a value map is serialised as an ordered **list** of `{"from": …, "to": …}`
  pairs (so the transform dataclass can stay frozen and hashable), and a list's order is
  preserved by every serialiser. Two humans entering the same three mappings in different
  orders would otherwise produce two different hashes for the same instruction. Sorting is
  safe because a `map_values` table is a lookup: `table_dict` turns it into a dict, so
  order never carried meaning.
* **Decimals as strings.** Nothing in a mapping document is a float, and a `Decimal` that
  reached `json.dumps` would raise rather than quietly rounding.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, cast

#: The key a value-map pair is sorted by.
_PAIR_SORT_KEY = "from"


def _default(value: object) -> str:
    if isinstance(value, Decimal):
        return f"{value:f}"
    raise TypeError(f"{type(value).__name__} is not canonical-JSON serialisable")


def _normalise(value: Any) -> Any:
    """Recursively put a document into its one canonical shape.

    Lists keep their order - it is meaningful almost everywhere, a field list most of all -
    **except** a list of `{"from": …, "to": …}` pairs, which is a value map and is a set in
    everything but syntax.
    """
    # `cast`, not an annotation: `isinstance` on an `Any` narrows to `dict[Unknown, Unknown]`,
    # which strict mode will not accept assigned to a typed name. A JSON walker is the case
    # `cast` exists for - the shape is genuinely dynamic, and the alternative is a suppression
    # on every line of the recursion.
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return {key: _normalise(mapping[key]) for key in sorted(mapping)}
    if isinstance(value, list):
        items = [_normalise(item) for item in cast(list[Any], value)]
        if items and all(_is_pair(item) for item in items):
            return sorted(items, key=_pair_order)
        return items
    return value


def _is_pair(item: Any) -> bool:
    return isinstance(item, dict) and _PAIR_SORT_KEY in item


def _pair_order(pair: Any) -> str:
    """A value-map pair's sort key: its whole canonical text, so the order is total."""
    return json.dumps(pair, sort_keys=True)


def dumps(document: Any) -> str:
    """The document's canonical text: sorted, two-space indent, one trailing newline."""
    return json.dumps(_normalise(document), indent=2, sort_keys=True, default=_default) + "\n"


def sha256(document: Any) -> str:
    """The hash of the canonical text. Sixty-four lower-case hex characters."""
    return hashlib.sha256(dumps(document).encode("utf-8")).hexdigest()
