"""Bug-report tests for Phase 14's dashboard, found by the tester.

Both tests here are expected to **FAIL today**. Each documents a real defect found by reading
`backend/src/features/dashboard/repo.py` against `BACKEND.md` §4 ("every read and write checks the
record belongs to the caller") and the P14 plan's own Security section, which asserts "it resolves
the single demo customer and filters on `customer_id`... There is no unfiltered cross-customer
aggregate anywhere in it." That assertion does not match the committed SQL.

These are static, source-reading assertions rather than a live multi-customer reproduction,
deliberately: creating a second customer against the shared demo database this tester session is
using would pollute state other verification in this phase depends on. The bug is unambiguous
from the SQL text alone - there is no `customer_id` or `slug` predicate anywhere in `_LATEST`
or `_RUNS` -
and is only unobservable live today because exactly one customer row currently exists.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_REPO = REPO_ROOT / "backend" / "src" / "features" / "dashboard" / "repo.py"


def _sql_block(source: str, name: str) -> str:
    """The text inside `text(\"\"\" ... \"\"\")` for the named module-level query constant."""
    after_name = source.split(f"{name} = text(", 1)[1]
    return after_name.split('"""', 2)[1]


def test_bug_latest_run_query_has_no_customer_scope() -> None:
    """`_LATEST` joins `ingest_runs` to `customers` only to read the customer's *name* - it never
    filters by `customer_id` or `slug`. It picks the single most-recently-created
    `erp_invoice_lines` run **across every customer in the database**, and the dashboard route has
    no sign-in to otherwise narrow it. The instant a second customer exists, this public,
    unauthenticated route serves that customer's filename, row count, status and dollar figures to
    anyone who asks - a cross-tenant data leak on the one route in this phase whose own plan
    explicitly promised the opposite.
    """
    source = DASHBOARD_REPO.read_text(encoding="utf-8")
    block = _sql_block(source, "_LATEST").lower()
    # The join condition `c.id = r.customer_id` legitimately contains the substring "customer_id" -
    # that is not a scoping predicate, it is how the join to fetch the customer's name works at
    # all. The actual filter is the WHERE clause, and today it only narrows by file kind.
    where_clause = block.split("where", 1)[1].split("order by", 1)[0] if "where" in block else ""
    assert "slug" in where_clause or "customer_id" in where_clause, (
        f"_LATEST's WHERE clause is {where_clause.strip()!r} - it filters only by file kind, with "
        "no customer_id/slug predicate anywhere in the query. It is a global read of the single "
        "most-recently-created erp_invoice_lines run across every customer in the database, not a "
        "scoped read of one customer's latest run. BACKEND.md §4: 'every read and write checks the "
        "record belongs to the caller.' This route has no caller to check against (no sign-in), "
        "which makes the missing filter worse, not exempt from it - the P14 plan's own Security "
        "section commits to resolving *the* demo customer specifically and asserts 'there is no "
        "unfiltered cross-customer aggregate anywhere in it', which this query contradicts."
    )


def test_bug_runs_list_query_has_no_customer_scope() -> None:
    """Same defect as `_LATEST`, in the "recent runs" list `_RUNS` reads for the dashboard table:
    no join to `customers` at all, so it is a global, unscoped list of the most recent uploads from
    every customer in the database.
    """
    source = DASHBOARD_REPO.read_text(encoding="utf-8")
    block = _sql_block(source, "_RUNS").lower()
    assert "customer" in block, (
        "_RUNS joins only ingest_runs to source_files - it never reaches the customers table at "
        "all, so there is no possible predicate scoping it to one customer. Every visitor's "
        "dashboard 'Runs' table shows every customer's uploads, mixed together, ordered by "
        "created_at with no customer column to tell them apart."
    )
