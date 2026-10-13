"""Route-level, attack, and job tests for Phase 2 (uploads, parse job, runs list).

mvp.md P2 steps 2.1-2.4 name explicit accept criteria; `pytest` had **zero** coverage of
the feature routes before this file (the handover's own "known gap"), so these tests
exercise the real `docker compose` stack over HTTP - the same black-box surface a
client hits - rather than importing `src.main:app` into the pytest process, which would
require fighting the process-wide `config.load()` cache over `UPLOAD_DIR`.

Skips cleanly (not fail) if the compose stack is not reachable, matching
`test_compose_stack.py`'s own pattern.
"""

from __future__ import annotations

import gzip
import io
import os
import shutil
import subprocess
import time
import tracemalloc
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from src.lib.errors import TooManyRows, Unparseable
from src.lib.ids import uuid7
from src.lib.parsers.csv_reader import read_csv_shape
from src.lib.parsers.detect import detect_format
from src.lib.parsers.fwf_reader import read_fixed_width_shape

API_BASE = "http://localhost:8000"
APP_DB_URL = "postgresql://speedy:speedy@127.0.0.1:5432/speedy_onboard"
ADMIN_DB_URL = "postgresql://speedy_admin:speedy@127.0.0.1:5432/speedy_onboard"

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CSV = REPO_ROOT / "backend" / "fixtures" / "sample" / "month1_invoices.csv"
FULL_FWF_GZ = REPO_ROOT / "backend" / "fixtures" / "full" / "month1_invoices.txt.gz"


def _db(url: str) -> psycopg.Connection[tuple[Any, ...]]:
    """`psycopg.connect`, autocommit on, with an explicit row type.

    Without a type parameter, pyright's strict mode resolves the connection's row type
    as `Unknown` rather than plain `Any`, which cascades into every `.fetchone()` /
    `.fetchall()` downstream. One typed helper instead of a suppression per call site.
    """
    return psycopg.connect(url, autocommit=True)


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True, timeout=10
    )
    return result.returncode == 0


@pytest.fixture
def live_api() -> None:
    """Skip, don't fail, when the compose stack isn't up on :8000 / :5432.

    Mirrors `test_compose_stack.py`'s `running_compose_stack` fixture; duplicated
    rather than imported because that fixture is tied to bringing the stack up, and
    these tests only need to know it is already reachable (they must not spend a test
    run's failure budget bringing Docker up by surprise).
    """
    if not _compose_available():
        pytest.skip("docker compose is not available in this environment")
    try:
        httpx.get(f"{API_BASE}/healthz", timeout=2)
    except httpx.HTTPError:
        pytest.skip("API is not reachable on :8000 - run `docker compose up -d`")


def _restart_api_and_wait(*, timeout: float = 20) -> None:
    subprocess.run(
        ["docker", "compose", "restart", "api"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{API_BASE}/healthz", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)


def _post_upload(
    file_path: Path,
    *,
    customer_id: str,
    kind: str = "erp_invoice_lines",
    filename: str | None = None,
) -> httpx.Response:
    """POST /v1/uploads, retried once (after a container restart) on a specific,
    diagnosed-not-guessed staleness, never blindly.

    `src/jobs/tasks.py::ensure_queue_open` opens procrastinate's async connection pool
    once per process and never closes it. When `test_migrations.py` runs earlier in the
    same `pytest` session, its `downgrade base` / `upgrade head` drops and recreates
    every procrastinate function - and `api`'s already-open pool keeps the old, now-
    invalid function OIDs, so `defer_parse()` inside this route raises and the handler
    maps it to `503 dependency_unavailable`. `test_migrations.py`'s own `_ensure_head()`
    already restarts `worker` for the identical reason (its docstring names the crash);
    `api` needs the same remedy and is the one service that file does not restart -
    confirmed live: `GET /v1/customers` (no procrastinate involved) reports healthy
    immediately after `test_migrations.py`, while this exact POST still 503s. A blind
    retry-on-503 would be wrong for every *other* reason a dependency can be down; this
    one is narrow (retries only this specific route, only once, only after a restart)
    and safe (a 503 means nothing was written - BACKEND.md §7 allows retrying a call
    that never succeeded).
    """

    def _attempt() -> httpx.Response:
        with file_path.open("rb") as handle:
            return httpx.post(
                f"{API_BASE}/v1/uploads",
                data={"customerId": customer_id, "kind": kind},
                files={"file": (filename or file_path.name, handle, "text/csv")},
                timeout=10,
            )

    response = _attempt()
    if response.status_code == 503:
        _restart_api_and_wait()
        response = _attempt()
    return response


@pytest.fixture
def customer(live_api: None) -> Generator[str]:
    """A customer row via the admin role, self-deleting (cascades to its runs/files)."""
    del live_api
    customer_id = str(uuid7())
    with _db(ADMIN_DB_URL) as conn:
        conn.execute(
            "insert into customers (id, name, slug) values (%s, %s, %s)",
            (customer_id, "Route Test Co", f"route-test-{uuid.uuid4().hex[:8]}"),
        )
    try:
        yield customer_id
    finally:
        with _db(ADMIN_DB_URL) as conn:
            conn.execute("delete from customers where id = %s", (customer_id,))


# --------------------------------------------------------------------------------------
# 2.2 - upload route contract and attacks
# --------------------------------------------------------------------------------------


def test_upload_of_the_committed_csv_sample_is_accepted_and_the_file_row_has_a_random_name(
    customer: str,
) -> None:
    _reset_rate_limits()
    response = _post_upload(SAMPLE_CSV, customer_id=customer)

    assert response.status_code == 202
    body = response.json()["data"]
    assert "ingestRunId" in body
    assert body["statusUrl"] == f"/v1/ingest-runs/{body['ingestRunId']}"

    with _db(APP_DB_URL) as conn:
        row = conn.execute(
            "select original_filename, stored_name from source_files where id = %s",
            (body["sourceFileId"],),
        ).fetchone()
    assert row is not None
    original_filename, stored_name = row
    assert original_filename == "month1_invoices.csv"
    assert stored_name != original_filename
    assert "." not in stored_name  # extensionless, per the storage seam's contract


def test_unknown_customer_id_is_404_and_writes_no_source_file_row(live_api: None) -> None:
    del live_api
    _reset_rate_limits()
    bogus = str(uuid7())
    before = _source_file_count()

    with SAMPLE_CSV.open("rb") as handle:
        response = httpx.post(
            f"{API_BASE}/v1/uploads",
            data={"customerId": bogus, "kind": "erp_invoice_lines"},
            files={"file": ("month1_invoices.csv", handle, "text/csv")},
            timeout=10,
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert _source_file_count() == before


def test_a_pdf_renamed_csv_is_422_unsupported_file_type_with_a_file_field_error(
    customer: str,
) -> None:
    _reset_rate_limits()
    pdf_bytes = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    before = _source_file_count()

    response = httpx.post(
        f"{API_BASE}/v1/uploads",
        data={"customerId": customer, "kind": "erp_invoice_lines"},
        files={"file": ("export.csv", io.BytesIO(pdf_bytes), "text/csv")},
        timeout=10,
    )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "unsupported_file_type"
    assert "file" in body["fields"]
    assert _source_file_count() == before


def _source_file_count() -> int:
    with _db(APP_DB_URL) as conn:
        row = conn.execute("select count(*) from source_files").fetchone()
    assert row is not None
    return int(row[0])


def _reset_rate_limits() -> None:
    """Give an upload-route test a full budget of its own.

    Every test in this file shares one source IP (the host, via Docker's loopback),
    so without this, whichever test runs first - including a run of this *same* file a
    minute ago - eats into every later test's 20/hour allowance and the rate-limit test
    itself needs a deterministic, empty window to trip predictably at request 21.
    """
    with _db(ADMIN_DB_URL) as conn:
        conn.execute("delete from rate_limits where key like 'upload:%'")


# --------------------------------------------------------------------------------------
# 2.1 - rate limit: the 429 must carry Retry-After (BACKEND.md §3's table says so, and
# step 2.1's own accept criterion names it explicitly). It is a bug report, not a
# weakened assertion: this test is EXPECTED to fail until the header is added.
# --------------------------------------------------------------------------------------


def test_429_response_carries_a_retry_after_header(live_api: None) -> None:
    """mvp.md 2.1: "429 with Retry-After set to the seconds remaining in the window."

    `src/lib/errors.py::RateLimited` and the generic `AppError` carry no mechanism to
    attach extra headers, and `src/lib/ratelimit.py::enforce` computes
    `retry_after_seconds` only to fold it into the human-readable message - it is never
    set as a response header anywhere in `src/`. Confirmed live: 20 uploads from one
    untrusted-peer IP followed by a 21st returns 429 with no `Retry-After` header at
    all. BACKEND.md §3's status table lists `429 | Too many requests; includes
    Retry-After` as a hard contract line, not a suggestion.
    """
    del live_api
    _reset_rate_limits()
    bogus = str(uuid7())  # a customer that doesn't exist: fails fast, no disk write,
    # and still passes through `ratelimit.enforce` first (see service.py's ordering).

    last_response: httpx.Response | None = None
    for _ in range(25):  # comfortably past the 20/hour cap regardless of prior count
        with SAMPLE_CSV.open("rb") as handle:
            last_response = httpx.post(
                f"{API_BASE}/v1/uploads",
                data={"customerId": bogus, "kind": "erp_invoice_lines"},
                files={"file": ("x.csv", handle, "text/csv")},
                timeout=10,
            )
        if last_response.status_code == 429:
            break

    assert last_response is not None
    assert last_response.status_code == 429, "expected the rate limit to trip within 25 tries"
    assert "retry-after" in {k.lower() for k in last_response.headers}, (
        "429 response has no Retry-After header - BACKEND.md §3 and mvp.md step 2.1's "
        "own accept criterion both require one"
    )


# --------------------------------------------------------------------------------------
# 2.4 - runs list: pagination, clamping, malformed cursor, unknown id, index usage
# --------------------------------------------------------------------------------------


def test_limit_over_100_is_clamped_to_100(customer: str) -> None:
    response = httpx.get(
        f"{API_BASE}/v1/ingest-runs", params={"customerId": customer, "limit": 5000}, timeout=10
    )
    assert response.status_code == 200
    # Clamped, not rejected (BACKEND.md §3: "default 25, max 100") - an empty result
    # for this fresh customer still proves the request was accepted, not 400'd.
    assert response.json()["nextCursor"] is None


def test_unknown_run_id_is_404_naming_no_internal_detail(live_api: None) -> None:
    del live_api
    bogus = str(uuid7())
    response = httpx.get(f"{API_BASE}/v1/ingest-runs/{bogus}", timeout=10)

    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "not_found"
    raw = str(body)
    for needle in ("ingest_runs", "select", "SELECT", "psycopg", "Traceback"):
        assert needle not in raw


def test_a_sql_injection_cursor_is_400_malformed_request_and_the_table_survives(
    live_api: None,
) -> None:
    del live_api
    response = httpx.get(
        f"{API_BASE}/v1/ingest-runs",
        params={"cursor": "';DROP TABLE source_files;--"},
        timeout=10,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed_request"

    with _db(APP_DB_URL) as conn:
        row = conn.execute(
            "select exists (select 1 from information_schema.tables "
            "where table_name = 'source_files')"
        ).fetchone()
    assert row is not None
    assert row[0] is True


def test_list_query_uses_the_customer_created_at_index_at_10k_rows(customer: str) -> None:
    """mvp.md 2.4 (speed): EXPLAIN shows an index scan on (customer_id, created_at desc).

    Seeded directly via `generate_series` rather than 10,000 real uploads - the claim
    under test is the query plan, not the upload path.
    """
    with _db(ADMIN_DB_URL) as conn:
        conn.execute(
            """
            insert into source_files (
                id, customer_id, kind, original_filename, stored_name, byte_size,
                sha256, sniffed_type, format, uploader_ip_hash, row_count
            )
            select
                gen_random_uuid(), %(cust)s, 'erp_invoice_lines',
                'seed.csv', gen_random_uuid()::text, 100, 'deadbeef', 'text/csv', 'csv',
                'seedhash', 10
            from generate_series(1, 10000)
            """,
            {"cust": customer},
        )
        file_ids = [
            r[0]
            for r in conn.execute(
                "select id from source_files where customer_id = %(cust)s",
                {"cust": customer},
            ).fetchall()
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "insert into ingest_runs (id, source_file_id, customer_id, status) "
                "values (gen_random_uuid(), %s, %s, 'parsed')",
                [(fid, customer) for fid in file_ids],
            )

        plan_rows = conn.execute(
            "explain select * from ingest_runs where customer_id = %s "
            "order by created_at desc limit 25",
            (customer,),
        ).fetchall()
    plan_text = "\n".join(r[0] for r in plan_rows)
    assert "ix_ingest_runs_customer_created" in plan_text, plan_text
    assert "Index Scan" in plan_text or "Index Only Scan" in plan_text, plan_text


# --------------------------------------------------------------------------------------
# 2.3 - the parse job: idempotency, the row cap while streaming, the 15s budget
# --------------------------------------------------------------------------------------


def test_defer_parse_twice_is_a_single_write_no_duplicate_run(customer: str) -> None:
    """mvp.md 2.3: "given the same job run twice ... row_count is written once".

    Imported lazily so this module's collection doesn't require `src.jobs.tasks`'
    procrastinate app to be importable outside this test's live-DB context.
    """
    from src import config
    from src.jobs.tasks import parse_source_file

    _reset_rate_limits()
    upload = _post_upload(SAMPLE_CSV, customer_id=customer).json()["data"]

    run_id = upload["ingestRunId"]
    run: dict[str, Any] = {}
    reached_terminal = False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}", timeout=10).json()["data"]
        if run["isSettled"]:
            reached_terminal = True
            break
        time.sleep(0.3)
    assert reached_terminal, "run never reached a terminal status within 10s"
    # Poll on `isSettled`, never on a status list. This test used to wait for
    # `("parsed", "failed")`; Phase 3 chained profiling onto a successful parse, which
    # demoted `parsed` to a waypoint and left this loop spinning until it timed out.
    # The server owns the vocabulary (`runs/schemas.py:TERMINAL_STATUSES`), so a client
    # that restates it - a test is a client - goes stale silently the next time the
    # pipeline grows a step. A successful CSV parse now settles at `profiled`.
    assert run["status"] == "profiled", run

    # Redeliver the already-completed job by hand, in-process. `parse_source_file`
    # opens its own plain psycopg connection from `config.load().database_url`, which
    # this test session has pointed at an address nothing listens on (so other tests
    # can prove a 503-on-DB-down path) - point it at the real local Postgres just for
    # this call, then restore it so no other test in the session is affected.
    previous_db_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "postgresql+psycopg://speedy:speedy@127.0.0.1:5432/speedy_onboard"
    config.load.cache_clear()
    try:
        result = parse_source_file(ingest_run_id=run_id)
    finally:
        if previous_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_db_url
        config.load.cache_clear()

    assert result.get("skipped") is True

    with _db(APP_DB_URL) as conn:
        row_count_row = conn.execute(
            "select row_count from source_files where id = %s", (upload["sourceFileId"],)
        ).fetchone()
        run_count_row = conn.execute(
            "select count(*) from ingest_runs where id = %s", (run_id,)
        ).fetchone()
    assert row_count_row is not None and row_count_row[0] == 500
    assert run_count_row is not None and run_count_row[0] == 1


def test_300k_row_csv_against_the_250k_cap_fails_with_flat_memory() -> None:
    """mvp.md 2.3 (denial + speed): the cap must be enforced per-row, not after loading.

    Builds a 300,000-row CSV (well past `max_upload_rows=250_000`) and measures peak
    additional memory via `tracemalloc` while `read_csv_shape` runs. A loaded-then-
    checked implementation would allocate on the order of the whole file (tens of MB of
    Python list/str objects); a truly streaming one should stay in the single-digit MB.
    """
    tmp_dir = Path("/tmp/speedy_tester_rowcap")
    tmp_dir.mkdir(exist_ok=True)
    path = tmp_dir / "big.csv"
    header = "a,b,c,d,e\n"
    row = "1,2,3,4,5\n"
    with path.open("w") as handle:
        handle.write(header)
        for _ in range(300_000):
            handle.write(row)

    tracemalloc.start()
    try:
        with pytest.raises(TooManyRows) as excinfo:
            read_csv_shape(path, max_rows=250_000)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        path.unlink(missing_ok=True)

    assert "250,000" in excinfo.value.message
    peak_mb = peak / (1024 * 1024)
    assert peak_mb < 20, (
        f"peak traced memory was {peak_mb:.1f} MB - looks like buffering, not streaming"
    )


def test_a_ragged_csv_names_the_offending_line() -> None:
    tmp_dir = Path("/tmp/speedy_tester_rowcap")
    tmp_dir.mkdir(exist_ok=True)
    path = tmp_dir / "ragged.csv"
    lines = ["a,b,c"] + ["1,2,3"] * 410 + ["1,2,3,4"] + ["1,2,3"] * 10
    path.write_text("\n".join(lines) + "\n")

    try:
        with pytest.raises(Unparseable) as excinfo:
            read_csv_shape(path, max_rows=250_000)
    finally:
        path.unlink(missing_ok=True)

    assert "412" in excinfo.value.message


def test_50k_fixed_width_fixture_parses_under_the_15_second_budget() -> None:
    """mvp.md 2.3 (speed): "the regenerated 50k fixed-width file ... under 15 s"."""
    tmp_dir = Path("/tmp/speedy_tester_fwf")
    tmp_dir.mkdir(exist_ok=True)
    path = tmp_dir / "month1_invoices.txt"
    with gzip.open(FULL_FWF_GZ, "rb") as gz, path.open("wb") as out:
        shutil.copyfileobj(gz, out)

    try:
        start = time.monotonic()
        shape = read_fixed_width_shape(path, max_rows=250_000)
        elapsed = time.monotonic() - start
    finally:
        path.unlink(missing_ok=True)

    assert shape.row_count > 45_000, shape
    assert elapsed < 15, f"parse took {elapsed:.2f}s, over the 15s budget"


# --------------------------------------------------------------------------------------
# detect_format: a realistic file where delimiter-consistency-first gets it wrong
# --------------------------------------------------------------------------------------


def test_detect_format_now_reads_the_one_comma_fixed_width_file_as_fixed_width() -> None:
    """Regression anchor for the fix: a low, constant comma count no longer beats
    identical record lengths.

    This used to be a bug report (`csv` for a file that is, in fact, fixed-width): an
    AS/400 customer-name field formatted "LASTNAME, FIRST" carries exactly one comma on
    every record by construction, not by accident, and `detect_format` trusted the
    comma count over the record-length signal. The builder's fix: when the comma count
    is constant *and low* (<=2) *and* every sampled record is the same length, the
    length signal wins. Same fixture as before the fix; the assertion is now the
    opposite of what it was.
    """
    records: list[str] = []
    for i in range(20):
        name_field = f"SMITH, J{i:02d}".ljust(20)
        amount_field = f"{(i + 1) * 111}".rjust(10)
        records.append(f"{i:06d}{name_field}{amount_field}")

    assert len({len(r) for r in records}) == 1, "fixture is not actually fixed-width"
    head = ("\n".join(records) + "\n").encode("ascii")

    assert detect_format(head) == "fixed_width"


def test_detect_format_still_reads_a_narrow_csv_with_naturally_varying_row_lengths_as_csv() -> None:
    """The fix's blast radius, checked: an ordinary narrow CSV is unaffected.

    A real two-column CSV's row lengths vary because its values do (names and amounts
    are rarely all the same width) - so it never satisfies the fixed-width tie-break's
    `uniform_length` condition, and the original comma-count signal still wins. This is
    the case the builder says they verified by hand; pinned here so it stays true.
    """
    names = ["Grove", "Beta Group", "Cedar Grove Dining", "X", "Clearfield Catering Co"]
    records = [f"{name},{(i + 1) * 111}" for i, name in enumerate(names)] * 4  # 20 lines

    assert len({len(r) for r in records}) > 1, "fixture is not actually length-varying"
    head = ("\n".join(records) + "\n").encode("ascii")

    assert detect_format(head) == "csv"


def test_detect_format_misreads_a_synthetic_uniform_length_two_column_csv_as_fixed_width() -> None:
    """The fix's documented, accepted cost, made executable rather than just asserted.

    A genuine two-column CSV whose every sampled row happens to be *exactly* the same
    length (synthetic - real exports vary) now reads as `fixed_width` instead of `csv`,
    because identical lengths plus a low constant comma count favours the fixed-width
    branch. The builder's call: this bites artificially uniform files, not real
    exports, and P4 gives a human the override. This test exists so a future change to
    the heuristic has to touch this file - the trade-off should be a deliberate choice,
    not a side effect nobody notices.
    """
    records = [f"NAME{i:02d}, X,{(i + 1):04d}" for i in range(20)]

    assert len({len(r) for r in records}) == 1, "fixture is not actually uniform-length"
    head = ("\n".join(records) + "\n").encode("ascii")

    assert detect_format(head) == "fixed_width"
