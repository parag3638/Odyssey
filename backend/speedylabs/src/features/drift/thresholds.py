"""Where a difference becomes a finding, and the margins that justify each number.

Published constants, not configuration - the same call P8 made for its extension tolerance, for the
same reason: a deployment-wide environment variable is the wrong axis for what is really a
per-customer commercial judgement, and a threshold nobody can see is worse than one in a table.
Every
one of these is carried into the finding's `detail.threshold` and rendered beside the numbers it
compared, so a human judges rather than trusting a verdict.

Measured on the committed fixtures (CSV, month 1 -> drifted month 2), firing and nearest non-firing,
so none of these is tuned to the fixture:

| threshold          | fires                      | margin | nearest non-firing      | margin |
|--------------------|----------------------------|--------|-------------------------|--------|
| MIX_SHARE_POINTS   | `UMCODE` CS -35.94pp       | 3.59x  | `UMCODE` LB +0.51pp     | 0.05x  |
| MIX_SHARE_POINTS   | `CSEMUL` "2" -23.39pp      | 2.34x  | `ALWFLG` N +8.21pp      | 0.82x  |
| MEAN_RELATIVE      | `SHPWGT` +34.53%           | 1.73x  | `LINAMT` +7.85%         | 0.39x  |
| NULL_RATE_POINTS   | - none on any fixture      | -      | `ALWPRC` +8.20pp        | 0.82x  |
| RENAME_NAME_RATIO  | `SHPUNT`->`SHPQTY` 0.667   | 1.11x  | -                       | -      |

Two of those are thin, and both stay where they are for a stated reason:

* `ALWPRC` at 0.82x of `NULL_RATE_POINTS` is tempting to chase, and must not be: its null rate is
  **identical** in the drifted and the no-drift month-2 files, so a 9pp threshold would fire on both
  and the control would stop being a control. That signal is already carried by `type_changed`.
* `RENAME_NAME_RATIO`'s 1.11x is the thinnest margin in the phase. A gate at 0.70 would miss
  `SHPUNT` -> `SHPQTY` and report an add plus a remove instead, which the oracle's empty
  added/removed column lists would catch - so the test asserts the pairing by its **reason** (same
  index, equal type, ratio >= 0.60) and the measured 0.667, not only the outcome.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

#: Absolute move in a column's null share before it is a finding. Points, not percent: 0.10 means
#: ten percentage points, which is what `null_pct` is expressed in.
NULL_RATE_POINTS: Final = Decimal("0.10")

#: Absolute move in any single value's share of a column.
MIX_SHARE_POINTS: Final = Decimal("0.10")

#: Relative move in a column's mean. Relative, because a mean's scale is the column's own - twenty
#: percent means the same thing for a weight in pounds and an amount in dollars.
MEAN_RELATIVE: Final = Decimal("0.20")

#: Baseline distinct values below which a column is treated as an enumeration, so a value it has
#: never carried is worth reporting. Above it, a new value is ordinary.
ENUM_MAX_DISTINCT: Final = 20

#: `difflib.SequenceMatcher` ratio above which two names are the same column renamed.
RENAME_NAME_RATIO: Final = Decimal("0.60")
