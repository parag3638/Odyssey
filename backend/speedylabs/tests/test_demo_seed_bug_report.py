"""Bug-report tests for Phase 15 (demo seed/reset) and Phase 14 (dashboard), found by the tester.

**History, not current state**: every test here was written to document a real defect found by
driving the live `docker compose` stack, and at the time each one was added it failed. All seven
now pass against this codebase - `seed.py` grew its fifth file and full pipeline, the approved
spec fixture was committed, `_LATEST` picks the run with variance data rather than the newest
upload, `singleflight.py` exists, and the reset route defers to the worker - which makes these
regression tests for those fixes rather than open bug reports. Do not loosen an assertion to make
it pass; if one starts failing, the regression it guards has come back.

Pattern follows `tests/test_uploads_and_runs_routes.py`: black-box over HTTP against the real
compose stack plus a direct `psycopg` connection for verification, skipping cleanly when the stack
is not reachable.

**The first three tests below establish the seeded demo themselves**, via the `seeded_demo`
fixture (`tests/conftest.py`), rather than reading whatever a previous reset happened to leave
behind - the original version of this file read existing state instead, as a deliberate
rate-limit-budget trade-off, but that stopped being safe once this suite grew a migrations test
(`test_migrations.py`) that runs `alembic downgrade base` against this same live database:
depending on collection order, state these three tests read could already have been wiped by the
time they ran, and the gap showed up asymmetrically - two tests skipped cleanly (`if not
state["seeded"]:` / `if not rows:`) while the third had no such guard and hard-failed instead.
Both outcomes were really the same cause; neither is a defect in the thing being described any
more now that `tests.live.ensure_demo_seeded` makes the precondition true before any of the three
reads the database. `seeded_demo` is session-scoped, so the first of these three tests to run
pays the ~10-13s reset cost and the rest reuse it.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from tests.live import ADMIN_DB_URL, API_BASE, DEMO_SLUG, db, live_api

__all__ = ["live_api"]  # re-exported fixture

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"


def test_bug_seeder_produces_only_three_runs_not_five(live_api: None, seeded_demo: None) -> None:
    """P15's plan (docs/plans/phase-15-demo-reset.md) and `docs/decisions.md`'s 2026-10-08 P15
    entries both describe a seeded/reset demo with **five** runs: a profiled 500-row CSV, a
    profiled 500-row fixed-width file, a fully materialised/calculated/attributed 48,150-row ERP
    run, its matching legacy import, and a drift-blocked month-2 file. `seed.py` was fixed to
    produce all five (`SEED_FILES` in `src/features/demo/seed.py`); this is the regression test
    for that fix, now driven against a demo this test seeds itself rather than one it hopes a
    previous reset left behind.
    """
    del live_api
    del seeded_demo
    resp = httpx.get(f"{API_BASE}/v1/demo/state", timeout=5)
    assert resp.status_code == 200
    state = resp.json()["data"]
    assert state["seeded"] is True

    assert state["runCount"] == 5, (
        f"demo/state reports runCount={state['runCount']}, expected 5 per the P15 plan's seeded "
        "state (a profiled CSV, a profiled fixed-width file, a materialised+calculated+attributed "
        "ERP run, its legacy import, and a drift-blocked month-2 run)."
    )


def test_bug_seeded_demo_has_no_fully_processed_run(live_api: None, seeded_demo: None) -> None:
    """The demo's entire headline claim - 48,150 rows, blocking findings, a high variance
    explained percentage - requires at least one run that has been mapped, materialised, had
    rebates calculated, and had variances computed and attributed. Regression test for the same
    fix as above, from the status side rather than the count side.
    """
    del live_api
    del seeded_demo
    with db(ADMIN_DB_URL) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select r.status, count(*)
            from ingest_runs r
            join customers c on c.id = r.customer_id
            where c.slug = %s
            group by r.status
            """,
            (DEMO_SLUG,),
        )
        rows = dict(cur.fetchall())
    assert rows, "seeded_demo ran without error but the demo customer has no runs - investigate"

    advanced_statuses = {"mapped", "materializing", "materialized", "blocked", "ready"}
    advanced = {status: n for status, n in rows.items() if status in advanced_statuses}
    assert advanced, (
        f"no seeded run has reached a post-profile status; statuses present: {rows}. "
        "The P15 plan's run 3 (the full month-1 CSV) must reach `blocked` with rebates "
        "calculated and variances attributed for the dashboard's headline figures to mean "
        "anything."
    )


def test_bug_no_drift_blocked_run_in_seeded_demo(live_api: None, seeded_demo: None) -> None:
    """The P15 plan's run 5 is a month-2 file that drifts against run 3's approved spec and lands
    at `drift_blocked`, so a visitor sees drift protection without uploading anything.

    **This is the test the handover's `make check` investigation names as the one that actually
    fails from run order**: it has no `pytest.skip` guard for "demo not present", so on a session
    where an earlier test (`test_migrations.py`'s `downgrade base`) wiped the demo, `count` came
    back `0` and this assertion failed outright instead of skipping - the asymmetry the other two
    tests in this file hid by skipping instead. `seeded_demo` removes the precondition gap rather
    than adding a matching skip: a hard failure here once the demo is guaranteed fresh is a real
    regression, not a setup artifact.
    """
    del live_api
    del seeded_demo
    with db(ADMIN_DB_URL) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select count(*) from ingest_runs r
            join customers c on c.id = r.customer_id
            where c.slug = %s and r.status = 'drift_blocked'
            """,
            (DEMO_SLUG,),
        )
        row = cur.fetchone()
        assert row is not None
        count = int(row[0])
    assert count >= 1, (
        "expected at least one drift_blocked run in the seeded demo (the P15 plan's run 5); "
        "found none even after tests.live.seeded_demo ran."
    )


def test_bug_approved_spec_fixture_does_not_exist() -> None:
    """Plan step 15.1 commits `backend/fixtures/reference/mapping_spec.json` specifically so the
    seeder can give the demo's ERP runs a `mapped` status with `origin: 'spec'` for every field,
    with no OpenAI call. The file was never generated, so the seeder cannot have used it - the
    seeded runs rest at `profiled`, confirming the gap end to end rather than by this check alone.
    """
    spec_fixture = BACKEND_ROOT / "fixtures" / "reference" / "mapping_spec.json"
    assert spec_fixture.exists(), (
        f"{spec_fixture} does not exist. Plan step 15.1 ('The approved spec becomes a committed "
        "fixture') was never built, so seed.py has nothing to load a `mapping_specs` row from."
    )


def test_bug_dashboard_latest_run_picks_most_recent_upload_not_most_complete() -> None:
    """`backend/src/features/dashboard/repo.py::_LATEST` orders strictly by `r.created_at desc`
    among `erp_invoice_lines` runs. The P15 plan's own mandated seed order uploads the sample/
    fixed-width files first, then the fully-processed run, then (if it existed) a drift-blocked
    month-2 file *last* - so even a correctly-built seeder would make the dashboard's headline
    card show the LAST-uploaded ERP run, which per the plan's own forced order is never the
    fully-processed demo run. This is a design-level bug in `_LATEST`'s selection rule, independent
    of the seeder gap documented in the other tests in this file: it needs to pick the run that
    actually has variance data (e.g. order by `elapsed_ms_to_first_variance is not null desc`, or a
    dedicated "demo run" marker), not merely the newest upload.

    This test is a static code-reading assertion (no live call) so it keeps documenting the bug
    even after the seeder gap above is fixed.
    """
    repo_source = (BACKEND_ROOT / "src" / "features" / "dashboard" / "repo.py").read_text(
        encoding="utf-8"
    )
    latest_block = repo_source.split("_LATEST = text(")[1].split('"""\n)')[0]
    order_by_clause = latest_block.lower().split("order by", 1)[1].split("limit", 1)[0].strip()
    # The ORDER BY must not be a bare "most recent upload" with no preference for a run that has
    # actually produced variance data. If this comes to order on `elapsed_ms_to_first_variance`
    # (or an equivalent "has this run finished" signal) ahead of `created_at`, the bug is fixed and
    # this assertion should be rewritten rather than deleted outright.
    assert order_by_clause != "r.created_at desc", (
        f"_LATEST's ORDER BY is exactly {order_by_clause!r} - it orders only by r.created_at, so "
        "given the P15 plan's own forced seed order (samples/fixed-width first, the "
        "fully-processed run next, any drift run last) the dashboard headline will show the "
        "LAST-uploaded run's (probably empty) figures rather than the demo run with real numbers, "
        "by construction - not merely because the seeder is currently incomplete."
    )


def test_bug_no_postgres_advisory_lock_for_demo_reset_single_flight() -> None:
    """The P15 plan specifies a session-scoped Postgres advisory lock
    (`lib/singleflight.py`, `pg_try_advisory_lock`) for the reset's single-flight guard,
    specifically so a killed process releases it rather than wedging the feature behind a row
    nobody will clear, and so the guard works across multiple API replicas/processes.

    `backend/src/features/demo/service.py` uses a plain in-process `asyncio.Lock()` instead.
    That protects against two near-simultaneous requests hitting the *same* process, but does
    nothing at all across multiple uvicorn workers or horizontally-scaled API replicas (the
    production target per `mvp.md`/P17's Northflank deploy) - two replicas could run a reset each,
    concurrently, with neither seeing the other's lock.
    """
    singleflight = BACKEND_ROOT / "src" / "lib" / "singleflight.py"
    assert singleflight.exists(), (
        f"{singleflight} does not exist. backend/src/features/demo/service.py uses "
        "`asyncio.Lock()` (process-local) instead of the plan's Postgres `pg_try_advisory_lock` "
        "(cross-process), so the single-flight guard does not hold across multiple API replicas."
    )


def test_bug_reset_route_claims_202_but_does_all_work_synchronously() -> None:
    """The P15 plan's API section says `POST /v1/demo/reset` returns `202` "with the work on the
    queue", and its Speed section argues at length that the reset must run as a job rather than in
    the request specifically because it drops ~145,000 rows under a live worker. Measured: the
    route returns in well under a second because `service.start_reset` awaits the entire clear +
    reseed in-line inside the request handler - there is no job, no queue hand-off, and the
    `statusUrl` in the response (`/v1/demo/state`) is a static string, not a per-reset job-status
    resource. A 202 response is a promise that the body's status URL will report progress on work
    still running; here the work is already finished by the time the client reads the response.
    """
    service_source = (BACKEND_ROOT / "src" / "features" / "demo" / "service.py").read_text(
        encoding="utf-8"
    )
    start_reset_body = service_source.split("async def start_reset")[1]
    assert "defer" in start_reset_body or "enqueue" in start_reset_body, (
        "service.start_reset never defers work to the job queue - it awaits repo.clear(), "
        "the file sweep, and seed() in-line and returns 202 only after all of it has already "
        "happened. The plan's Speed section's argument for running this as a job (avoiding a "
        "~145,000-row delete blocking a live request, matching the worker-under-load precedent "
        "docs/progress.md records) is not implemented."
    )
