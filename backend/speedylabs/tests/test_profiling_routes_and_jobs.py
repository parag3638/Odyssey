"""Tester pass for mvp.md P3 steps 3.2 and 3.3 - the profiling job, repo and route.

Zero backend tests existed for `src/features/profiling/*`, `src/jobs/tasks.py`'s
`profile_source_file`, or the 0004 migration before this file - the Phase 3 commits
(`b6cc945`, `daf56a3`, `eee1307`, `77cd7dc`) added none. Every "asserted" word in
mvp.md 3.1-3.3's accept criteria (bounded memory, peak RSS, p95, exactly one query) was
therefore a claim in a commit message, not a repeatable check. This file is that check,
for the job and the route; `test_lib_profiling.py` covers the pure profiler (3.1).

Mirrors `test_uploads_and_runs_routes.py`'s shape and reuses its fixtures/helpers rather
than duplicating them (BACKEND.md §9's rule of two) - same live-stack-over-HTTP
approach, same skip-not-fail pattern when Compose is not reachable.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import event

from src.lib.ids import uuid7

# `customer` and `live_api` are pytest fixtures, not plain names - ruff's F811 sees a
# test function's `customer: str` parameter as "redefining" this import, but pytest
# resolves a fixture by the *name* in that module's scope, so the import has to use the
# original name (an alias would stop pytest from finding it). Each test parameter that
# triggers the false positive is annotated at the signature, not here.
from tests.test_uploads_and_runs_routes import (  # noqa: F401
    ADMIN_DB_URL,
    API_BASE,
    APP_DB_URL,
    SAMPLE_CSV,
    _db,  # pyright: ignore[reportPrivateUsage]
    _post_upload,  # pyright: ignore[reportPrivateUsage]
    _reset_rate_limits,  # pyright: ignore[reportPrivateUsage]
    customer,  # pyright: ignore[reportUnusedImport]
    live_api,  # pyright: ignore[reportUnusedImport]
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FULL_CSV_GZ = REPO_ROOT / "backend" / "fixtures" / "full" / "month1_invoices.csv.gz"
LIVE_DATABASE_URL = "postgresql+psycopg://speedy:speedy@127.0.0.1:5432/speedy_onboard"
TMP_UPLOAD_DIR = Path("/tmp/speedy_tester_profiling_uploads")


def _wait_for_settled(run_id: str, *, timeout: float = 15) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        run = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}", timeout=10).json()["data"]
        if run["isSettled"]:
            return run
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} never settled within {timeout}s: {run}")


def _insert_source_file_and_run(
    *, customer_id: str, stored_name: str, status: str, columns: int = 5
) -> tuple[str, str]:
    """A minimal valid (source_files, ingest_runs) pair, bypassing the upload route.

    Used where the scenario under test (a queued-but-not-parsed file, a file whose
    disk copy never existed) needs a precise, racy-free precondition rather than one
    assembled by timing an upload against a live worker.
    """
    file_id = str(uuid7())
    run_id = str(uuid7())
    with _db(ADMIN_DB_URL) as conn:
        conn.execute(
            """
            insert into source_files (
                id, customer_id, kind, original_filename, stored_name, byte_size,
                sha256, sniffed_type, format, uploader_ip_hash, row_count
            ) values (%s, %s, 'erp_invoice_lines', 'synthetic.csv', %s, 10,
                      'deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef',
                      'text/csv', 'csv', 'testhash', %s)
            """,
            (file_id, customer_id, stored_name, columns),
        )
        conn.execute(
            "insert into ingest_runs (id, source_file_id, customer_id, status) "
            "values (%s, %s, %s, %s)",
            (run_id, file_id, customer_id, status),
        )
    return file_id, run_id


def _insert_synthetic_profile(file_id: str, *, columns: int) -> None:
    with _db(ADMIN_DB_URL) as conn:
        for index in range(columns):
            conn.execute(
                """
                insert into column_profiles (
                    id, source_file_id, layout_version, column_index, column_name,
                    type_guess, row_count, null_count, null_pct, distinct_count,
                    distinct_capped, min_text, max_text, date_format, sample_values,
                    patterns
                ) values (
                    %s, %s, 1, %s, %s, 'text', 100, 0, 0, 5, false, 'a', 'z', null,
                    '["a", "b"]'::jsonb, '[{"signature": "A", "count": 5}]'::jsonb
                )
                """,
                (str(uuid7()), file_id, index, f"col_{index}"),
            )


# --------------------------------------------------------------------------------------
# 3.2, accept 1 - a parsed CSV, profiled: one column_profiles row per column, run
# settles at `profiled`.
# --------------------------------------------------------------------------------------


def test_profiling_a_parsed_csv_creates_one_row_per_column_and_settles_at_profiled(
    customer: str,  # noqa: F811 - fixture param, see import comment above
) -> None:
    _reset_rate_limits()
    upload = _post_upload(SAMPLE_CSV, customer_id=customer).json()["data"]
    run = _wait_for_settled(upload["ingestRunId"])

    assert run["status"] == "profiled"

    header = SAMPLE_CSV.read_text(encoding="ascii").splitlines()[0]
    expected_columns = len(header.split(","))

    with _db(APP_DB_URL) as conn:
        row = conn.execute(
            "select count(*) from column_profiles where source_file_id = %s",
            (upload["sourceFileId"],),
        ).fetchone()
    assert row is not None
    assert row[0] == expected_columns


# --------------------------------------------------------------------------------------
# 3.2, accept 2 - the job run twice: still exactly one row per column (upsert on the
# unique key), no duplicates.
# --------------------------------------------------------------------------------------


def test_profile_job_run_twice_leaves_exactly_one_row_per_column(
    customer: str,  # noqa: F811 - fixture param, see import comment above
) -> None:
    """Direct, in-process redelivery of `profile_source_file`, same pattern as
    `test_defer_parse_twice_is_a_single_write_no_duplicate_run` uses for the parse job.

    Built from a synthetic (source_files, ingest_runs) row rather than a real upload
    through the live container: the container's `UPLOAD_DIR` is a Docker-managed named
    volume, not a host path, so a file it wrote is not visible to this host-side
    process re-running the task function directly. Pointing `UPLOAD_DIR` at a host
    temp directory and writing the real sample CSV there gives the task the exact same
    thing it would see in production - an openable file at the stored name - without
    that mismatch.
    """
    from src import config
    from src.jobs.tasks import profile_source_file

    stored_name = uuid7().hex
    file_id, run_id = _insert_source_file_and_run(
        customer_id=customer, stored_name=stored_name, status="parsed"
    )

    previous_db_url = os.environ.get("DATABASE_URL")
    previous_upload_dir = os.environ.get("UPLOAD_DIR")
    os.environ["DATABASE_URL"] = LIVE_DATABASE_URL
    TMP_UPLOAD_DIR.mkdir(exist_ok=True)
    os.environ["UPLOAD_DIR"] = str(TMP_UPLOAD_DIR)
    config.load.cache_clear()
    shutil.copy(SAMPLE_CSV, TMP_UPLOAD_DIR / stored_name)
    try:
        first = profile_source_file(ingest_run_id=run_id)
        with _db(APP_DB_URL) as conn:
            before = conn.execute(
                "select count(*) from column_profiles where source_file_id = %s",
                (file_id,),
            ).fetchone()

        second = profile_source_file(ingest_run_id=run_id)
        with _db(APP_DB_URL) as conn:
            after = conn.execute(
                "select count(*) from column_profiles where source_file_id = %s",
                (file_id,),
            ).fetchone()
    finally:
        (TMP_UPLOAD_DIR / stored_name).unlink(missing_ok=True)
        if previous_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_db_url
        if previous_upload_dir is None:
            os.environ.pop("UPLOAD_DIR", None)
        else:
            os.environ["UPLOAD_DIR"] = previous_upload_dir
        config.load.cache_clear()

    assert first["status"] == "profiled"
    assert second["status"] == "profiled"
    assert before is not None and after is not None
    assert before[0] == after[0] > 0, "redelivery changed the row count - upsert is duplicating"


# --------------------------------------------------------------------------------------
# 3.2, accept (denial) - GET .../profile while the run is still `queued`: 409
# `profile_not_ready`, not a 500, with a message telling the user to wait.
# --------------------------------------------------------------------------------------


def test_get_profile_while_queued_is_409_profile_not_ready_with_a_wait_message(
    customer: str,  # noqa: F811 - fixture param, see import comment above
) -> None:
    file_id, _run_id = _insert_source_file_and_run(
        customer_id=customer, stored_name=uuid7().hex, status="queued"
    )

    response = httpx.get(f"{API_BASE}/v1/source-files/{file_id}/profile", timeout=10)

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "profile_not_ready"
    # §9: tells the user what happened and what to do, not a bare refusal.
    assert "profil" in body["message"].lower()
    for needle in ("Traceback", "psycopg", "select ", "SELECT "):
        assert needle not in str(body)


def test_get_profile_for_an_unknown_file_is_404_with_no_internal_detail(
    live_api: None,  # noqa: F811 - fixture param, see import comment above
) -> None:
    del live_api
    bogus = str(uuid7())
    response = httpx.get(f"{API_BASE}/v1/source-files/{bogus}/profile", timeout=10)

    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "not_found"
    raw = str(body)
    for needle in ("column_profiles", "select", "SELECT", "psycopg", "Traceback"):
        assert needle not in raw


# --------------------------------------------------------------------------------------
# 3.2, accept (failure) - the stored file is gone when the job runs: `failed` with
# `error_code='source_file_missing'`, and the message names no filesystem path.
# --------------------------------------------------------------------------------------


def test_profile_job_with_the_stored_file_missing_fails_without_naming_a_path(
    customer: str,  # noqa: F811 - fixture param, see import comment above
) -> None:
    from src import config
    from src.jobs.tasks import profile_source_file

    missing_stored_name = uuid7().hex  # never written to disk - "deleted before the
    # job runs" and "never existed" are the same precondition from the job's point of
    # view: both are `not path.exists()`.
    _file_id, run_id = _insert_source_file_and_run(
        customer_id=customer, stored_name=missing_stored_name, status="parsed"
    )

    previous_db_url = os.environ.get("DATABASE_URL")
    previous_upload_dir = os.environ.get("UPLOAD_DIR")
    os.environ["DATABASE_URL"] = LIVE_DATABASE_URL
    # A directory we know is writable and empty, rather than Settings' production
    # default (`/var/lib/speedy/uploads`), which `LocalStorage.__init__` would try to
    # `mkdir` on the host and likely cannot.
    TMP_UPLOAD_DIR.mkdir(exist_ok=True)
    os.environ["UPLOAD_DIR"] = str(TMP_UPLOAD_DIR)
    config.load.cache_clear()
    try:
        result = profile_source_file(ingest_run_id=run_id)
    finally:
        if previous_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_db_url
        if previous_upload_dir is None:
            os.environ.pop("UPLOAD_DIR", None)
        else:
            os.environ["UPLOAD_DIR"] = previous_upload_dir
        config.load.cache_clear()

    assert result["status"] == "failed"
    assert result["errorCode"] == "source_file_missing"

    with _db(APP_DB_URL) as conn:
        row = conn.execute(
            "select status, error_code, error_message from ingest_runs where id = %s",
            (run_id,),
        ).fetchone()
    assert row is not None
    status, error_code, error_message = row
    assert status == "failed"
    assert error_code == "source_file_missing"
    assert error_message is not None
    for needle in ("/", "\\", str(TMP_UPLOAD_DIR), missing_stored_name, "var/lib/speedy"):
        assert needle not in error_message, (
            f"error message names a filesystem path: {error_message!r}"
        )


# --------------------------------------------------------------------------------------
# 3.2, accept (speed) - the regenerated 50k-row CSV profiles under 15s with peak RSS
# under 300 MB, both asserted (not just measured once by hand and written into a commit
# message).
# --------------------------------------------------------------------------------------


#: Profiles the file in a *fresh* interpreter and reports its own peak RSS.
#:
#: Measured in a child process because `ru_maxrss` is a high-water mark that never
#: decreases: run inside pytest, it reports the peak of a process that has already
#: imported pandas, SQLAlchemy, FastAPI and every test module, so it answers "how big did
#: the test runner get?" and not "how much memory does profiling need?". The first
#: version of this test measured 449 MB before profiling and 449 MB after - identical,
#: which proves profiling contributed nothing to that peak and that the number was never
#: attributable to the code under test. A child that imports only the profiler makes the
#: measurement mean what the criterion says.
_PROFILE_IN_CHILD = """
import json, resource, sys, time
from pathlib import Path

from src.lib.parsers.csv_reader import iter_csv_rows
from src.lib.profiling.accumulator import profile_rows

path = Path(sys.argv[1])
start = time.monotonic()
profiles = []
for header, rows in iter_csv_rows(path):
    profiles = profile_rows(header, rows)
elapsed = time.monotonic() - start

peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
# ru_maxrss is kilobytes on Linux and bytes on macOS.
peak_mb = peak / 1024 if sys.platform == "linux" else peak / (1024 * 1024)

print(json.dumps({
    "columns": len(profiles),
    "rowCount": profiles[0].row_count if profiles else 0,
    "elapsed": elapsed,
    "peakMb": peak_mb,
}))
"""


def test_50k_csv_profiles_under_15s_with_peak_rss_under_300mb() -> None:
    tmp_dir = Path("/tmp/speedy_tester_profiling_speed")
    tmp_dir.mkdir(exist_ok=True)
    path = tmp_dir / "month1_invoices.csv"
    with gzip.open(FULL_CSV_GZ, "rb") as gz, path.open("wb") as out:
        shutil.copyfileobj(gz, out)

    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PROFILE_IN_CHILD, str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=Path(__file__).resolve().parent.parent,
        )
    finally:
        path.unlink(missing_ok=True)

    assert completed.returncode == 0, completed.stderr
    measured = json.loads(completed.stdout.strip().splitlines()[-1])

    assert measured["columns"] > 0
    assert measured["rowCount"] > 45_000, measured
    assert measured["elapsed"] < 15, (
        f"profiling took {measured['elapsed']:.2f}s, over the 15s budget"
    )
    # Now attributable: this is the peak of a process that did nothing but profile.
    assert measured["peakMb"] < 300, (
        f"peak RSS while profiling was {measured['peakMb']:.1f} MB, over the 300 MB "
        f"budget: {measured}"
    )


# --------------------------------------------------------------------------------------
# 3.3, accept (speed + no N+1) - a 90-column profile: p95 under 200ms over 20 live
# calls, and the route issues the same number of DB queries regardless of column count
# (asserted with a real query counter, not reasoned about from reading the repo).
# --------------------------------------------------------------------------------------


def test_90_column_profile_route_p95_under_200ms_over_20_calls(
    customer: str,  # noqa: F811 - fixture param, see import comment above
) -> None:
    file_id, _run_id_unused = _insert_source_file_and_run(
        customer_id=customer, stored_name=uuid7().hex, status="profiled", columns=90
    )
    _insert_synthetic_profile(file_id, columns=90)

    durations_ms: list[float] = []
    for _ in range(20):
        start = time.monotonic()
        response = httpx.get(f"{API_BASE}/v1/source-files/{file_id}/profile", timeout=10)
        durations_ms.append((time.monotonic() - start) * 1000)
        assert response.status_code == 200
        assert len(response.json()["data"]["columns"]) == 90

    durations_ms.sort()
    p95 = durations_ms[int(len(durations_ms) * 0.95) - 1]
    assert p95 < 200, f"p95 was {p95:.1f}ms over 20 calls: {durations_ms}"


def test_90_column_profile_issues_the_same_query_count_as_a_5_column_profile() -> None:
    """No N+1: the route's query count must not grow with the number of columns.

    Runs the real service/repo functions (`src.features.profiling.service.get_profile`)
    in-process against the live database with a SQLAlchemy `before_cursor_execute`
    listener counting statements - a white-box check, because nothing short of
    instrumenting the engine can tell "one query for ninety rows" apart from "ninety
    queries for ninety rows" from outside the process.
    """
    from src import config
    from src.features.profiling import service
    from src.lib import db

    customer_id = str(uuid7())
    with _db(ADMIN_DB_URL) as conn:
        conn.execute(
            "insert into customers (id, name, slug) values (%s, %s, %s)",
            (customer_id, "Query Count Co", f"query-count-{uuid7().hex[:8]}"),
        )

    try:
        file_5, _ = _insert_source_file_and_run(
            customer_id=customer_id, stored_name=uuid7().hex, status="profiled", columns=5
        )
        _insert_synthetic_profile(file_5, columns=5)
        file_90, _ = _insert_source_file_and_run(
            customer_id=customer_id, stored_name=uuid7().hex, status="profiled", columns=90
        )
        _insert_synthetic_profile(file_90, columns=90)

        previous_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = LIVE_DATABASE_URL
        config.load.cache_clear()

        async def _count_queries_for(file_id: str) -> int:
            db.init(config.load().database_url, pool_size=2)
            assert db._engine is not None  # pyright: ignore[reportPrivateUsage]
            statements: list[str] = []

            def _before_cursor_execute(
                conn: object,
                cursor: object,
                statement: str,
                parameters: object,
                context: object,
                executemany: bool,
            ) -> None:
                del conn, cursor, parameters, context, executemany
                statements.append(statement)

            event.listen(
                db._engine.sync_engine,  # pyright: ignore[reportPrivateUsage]
                "before_cursor_execute",
                _before_cursor_execute,
            )
            try:
                async with db.session() as session:
                    await service.get_profile(session, UUID(file_id))
            finally:
                event.remove(
                    db._engine.sync_engine,  # pyright: ignore[reportPrivateUsage]
                    "before_cursor_execute",
                    _before_cursor_execute,
                )
                await db.dispose()
            return len(statements)

        try:
            count_5 = asyncio.run(_count_queries_for(file_5))
            count_90 = asyncio.run(_count_queries_for(file_90))
        finally:
            if previous_db_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_db_url
            config.load.cache_clear()

        assert count_5 == count_90, (
            f"query count grew with column count: {count_5} queries for 5 columns vs "
            f"{count_90} for 90 - that is the N+1 the plan's accept criterion forbids"
        )
        # mvp.md 3.3's accept criterion, verbatim: "exactly one DB query, no N+1". Left
        # as a strict equality - not loosened to match what the route actually does -
        # so this fails and reports the gap rather than quietly agreeing with it. The
        # route in fact issues 2: `file_and_run_status` (existence/status check) plus
        # `profile_columns` (the data). There is no N+1 (count_5 == count_90 above
        # proves that part), but it is not "exactly one" as written.
        assert count_90 == 1, (
            f"GET .../profile issued {count_90} queries for one request; mvp.md 3.3's "
            "accept criterion says 'exactly one DB query, no N+1'. No N+1 is confirmed "
            "(count_5 == count_90), but the route is not down to a single query - it "
            "runs repo.file_and_run_status() then repo.profile_columns()."
        )
    finally:
        with _db(ADMIN_DB_URL) as conn:
            conn.execute("delete from customers where id = %s", (customer_id,))
