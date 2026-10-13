"""The one place the generator's seed lives.

Every random draw in the generator comes from a `random.Random` constructed with this
value — never the `random` module's global state, which another import could reseed.
Determinism is not a nicety here: the generator is the **oracle** the whole plan tests
against. If the 50k-row file changes between runs, every expected-variance figure and
every seeded-issue count silently stops meaning anything.
"""

from __future__ import annotations

from typing import Final

#: Fixed for the life of the project. Changing it invalidates every committed fixture
#: and every hand-calculated expectation derived from them.
SEED: Final = 20261003
