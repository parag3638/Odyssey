"""Reading a drift comparison, and the two decisions a human can make about it.

**Accept goes through P9's `approve_spec`.** There is one door into `mapping_specs`, so every
precondition P9 established applies here unchanged - complete, validated, fresh, no failing verdict
unless forced with a note - and accepting drift cannot become a side entrance that remembers a
mapping
nobody checked. It also makes the atomicity claim structurally true rather than carefully coded: the
spec insert and the decision share one transaction, so neither persists without the other.

**Reject marks, it never deletes.** The file, the run, the profiles and the findings all stay
readable. A rejection is a judgement about a file, and a judgement nobody can review afterwards is
not auditable - and approving a new spec later releases it, so it is reversible too.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.drift import repo
from src.features.drift.codes import DRIFT_CODES, TITLES
from src.features.specs import service as specs_service
from src.lib import logger
from src.lib.errors import (
    DriftAlreadyDecided,
    MalformedRequest,
    NoteRequired,
    NotFound,
    RuleBroken,
)
from src.lib.timeutil import now

log = logger.get_logger(__name__)

#: Order the UI reads findings in: blocking first, then the vocabulary's own order.
_SEVERITY_RANK: Final = {"blocking": 0, "warning": 1}
_CODE_RANK: Final = {code: index for index, code in enumerate(DRIFT_CODES)}

#: The longest note accepted on a decision. Matches `mapping_specs.approved_note`.
MAX_NOTE_LENGTH: Final = 2000


async def get_drift(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    """The comparison, the decision, and the side-by-side column list.

    Returns `comparable: false` with a reason rather than a `409`: "this is your first file from
    this
    customer" is a **state this screen renders**, not an error, and making every client turn a 409
    into prose would push that judgement outwards.
    """
    run = await repo.run_row(session, run_id)
    if run is None:
        raise NotFound("That run does not exist.")

    checks = await repo.checks(session, run_id)
    baseline_spec_id = run["drift_baseline_spec_id"]
    if not checks:
        standing = await repo.spec_standing(
            session, UUID(str(run["customer_id"])), UUID(str(run["source_file_id"]))
        )
        return {
            "ingest_run_id": run_id,
            "comparable": False,
            "reason": _not_compared_reason(run, standing),
            "decision": run["drift_decision"],
            "status": str(run["status"]),
            "baseline": None,
            "findings": [],
            "checks_run": [],
            "comparison": {"columns": []},
        }

    baseline = (
        await repo.baseline(session, UUID(str(baseline_spec_id))) if baseline_spec_id else None
    )
    found = sorted(await repo.findings(session, run_id), key=_finding_order)

    return {
        "ingest_run_id": run_id,
        "comparable": True,
        "reason": None,
        "decision": run["drift_decision"],
        "status": str(run["status"]),
        "customer_name": str(run["customer_name"]),
        "baseline": _baseline_out(baseline),
        "blocking_count": sum(1 for row in found if str(row["severity"]) == "blocking"),
        "warning_count": sum(1 for row in found if str(row["severity"]) == "warning"),
        "findings": [_finding_out(row) for row in found],
        "checks_run": [_check_out(row) for row in sorted(checks, key=_check_order)],
        "comparison": await _comparison(session, run, baseline),
    }


async def accept(
    session: AsyncSession,
    run_id: UUID,
    *,
    note: str,
    force: bool,
    client_ip_hash: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Approve this file's mapping as a new spec version, and release the run."""
    run = await _decidable(session, run_id)
    _require_note(note)

    # P9's door. Every precondition it enforces applies, and the spec insert shares this
    # transaction with the decision below - so a failure leaves neither.
    result = await specs_service.approve(
        session,
        source_file_id=UUID(str(run["source_file_id"])),
        note=note,
        force=force,
        client_ip_hash=client_ip_hash,
        idempotency_key=idempotency_key,
    )

    # `mapped`: the run has a draft from the spec it just approved, and materialising is the human's
    # next action. The findings stay - what was accepted is a matter of record.
    released = await repo.decide(
        session, run_id, decision="accepted", status="mapped", decided_at=now()
    )
    if not released:
        raise DriftAlreadyDecided(
            "This file's changes have already been decided. Reload the page to see the decision."
        )
    await session.commit()
    log.info(
        "drift_accepted",
        ingest_run_id=str(run_id),
        spec_version=result["spec"]["version"],
        created=result["created"],
    )
    return {"spec": result["spec"], "created": result["created"], "decision": "accepted"}


async def reject(
    session: AsyncSession, run_id: UUID, *, note: str, client_ip_hash: str
) -> dict[str, Any]:
    """Mark this file unusable. Nothing is deleted."""
    del client_ip_hash  # recorded by the request log; no spec is written, so nothing stores it
    await _decidable(session, run_id)
    _require_note(note)
    rejected = await repo.decide(
        session, run_id, decision="rejected", status="rejected", decided_at=now()
    )
    if not rejected:
        raise DriftAlreadyDecided(
            "This file's changes have already been decided. Reload the page to see the decision."
        )
    await session.commit()
    log.info("drift_rejected", ingest_run_id=str(run_id))
    return {"decision": "rejected", "note": note}


async def _decidable(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    run = await repo.run_row(session, run_id)
    if run is None:
        raise NotFound("That run does not exist.")
    decision = run["drift_decision"]
    if decision in ("accepted", "rejected"):
        raise DriftAlreadyDecided(
            f"This file's changes were already {decision}. That decision stands; upload a new file "
            "to start again."
        )
    if decision is None:
        raise RuleBroken(
            "This file has no changes waiting on a decision. It is already free to be materialised."
        )
    return run


def _require_note(note: str) -> None:
    """A decision with no explanation is a decision nobody can review."""
    if not note.strip():
        raise NoteRequired(
            "Say why in a note. This decision changes how every later file from this customer is "
            "read.",
            fields={"note": "Required."},
        )
    if len(note) > MAX_NOTE_LENGTH:
        raise MalformedRequest(
            f"The note must be {MAX_NOTE_LENGTH} characters or fewer.",
            fields={"note": "Too long."},
        )


def _finding_order(row: dict[str, Any]) -> tuple[int, int, str]:
    return (
        _SEVERITY_RANK.get(str(row["severity"]), len(_SEVERITY_RANK)),
        _CODE_RANK.get(str(row["drift_code"]), len(_CODE_RANK)),
        str(row["column_name"] or row["baseline_column_name"] or ""),
    )


def _check_order(row: dict[str, Any]) -> int:
    return _CODE_RANK.get(str(row["drift_code"]), len(_CODE_RANK))


def _finding_out(row: dict[str, Any]) -> dict[str, Any]:
    code = str(row["drift_code"])
    return {
        "drift_code": code,
        "title": TITLES.get(code, code),
        "severity": str(row["severity"]),
        "column_name": row["column_name"],
        "baseline_column_name": row["baseline_column_name"],
        "detail": dict(row["detail"]),
    }


def _check_out(row: dict[str, Any]) -> dict[str, Any]:
    code = str(row["drift_code"])
    return {
        "drift_code": code,
        "title": TITLES.get(code, code),
        "outcome": str(row["outcome"]),
        "reason": row["reason"],
        "columns_examined": int(row["columns_examined"]),
    }


def _baseline_out(baseline: dict[str, Any] | None) -> dict[str, Any] | None:
    if baseline is None:
        return None
    return {
        "approved_spec_version": int(baseline["version"]),
        "approved_at": baseline["approved_at"],
        "source_file_id": baseline["source_file_id"],
        "original_filename": baseline["original_filename"],
        "uploaded_at": baseline["uploaded_at"],
    }


async def _comparison(
    session: AsyncSession, run: dict[str, Any], baseline: dict[str, Any] | None
) -> dict[str, Any]:
    """The side-by-side column list: every column either side has, and what happened to it.

    One row per column rather than two lists, because the question a reader has is "what happened to
    this column?" and answering it from two lists is their work, not ours.
    """
    if baseline is None or baseline["source_file_id"] is None:
        return {"columns": []}
    baseline_version = baseline["approved_from_layout_version"]
    if baseline_version is None:
        return {"columns": []}

    current_version = await repo.current_layout_version(session, UUID(str(run["source_file_id"])))
    before = await repo.profile_columns(
        session, UUID(str(baseline["source_file_id"])), int(baseline_version)
    )
    after = (
        await repo.profile_columns(session, UUID(str(run["source_file_id"])), int(current_version))
        if current_version is not None
        else []
    )

    by_name_before = {str(row["column_name"]): row for row in before}
    by_name_after = {str(row["column_name"]): row for row in after}
    names = list(by_name_before) + [n for n in by_name_after if n not in by_name_before]

    columns: list[dict[str, Any]] = []
    for name in names:
        was, is_now = by_name_before.get(name), by_name_after.get(name)
        if was is not None and is_now is not None:
            status = "same" if was["type_guess"] == is_now["type_guess"] else "changed"
        elif was is None:
            status = "added"
        else:
            status = "removed"
        columns.append(
            {
                "name": name,
                "status": status,
                "baseline": _column_out(was),
                "current": _column_out(is_now),
            }
        )
    return {"columns": columns}


def _column_out(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    mean = (
        None
        if not row["numeric_count"] or row["numeric_sum"] is None
        else f"{row['numeric_sum'] / row['numeric_count']:.4f}"
    )
    return {
        "column_index": int(row["column_index"]),
        "type_guess": str(row["type_guess"]),
        "null_share": f"{row['null_pct']:f}",
        "distinct_count": int(row["distinct_count"]),
        "mean": mean,
    }


def _not_compared_reason(run: dict[str, Any], standing: dict[str, Any]) -> str:
    """Why this run has no comparison, in the words the screen shows.

    One sentence used to cover every case, and it was wrong on the demo's headline run: that
    file is the one the approved spec was made from, yet the page told the reader to go and
    approve a spec (2026-10-13).
    """
    if str(run.get("kind")) == "legacy_rebate":
        return (
            "Changes are tracked for ERP invoice exports. This is a legacy rebate file, which is "
            "compared on the Parallel run tab instead."
        )
    made_from = standing.get("made_from_version")
    if made_from is not None:
        return (
            f"This is the file mapping spec v{made_from} was approved from, so it is the baseline: "
            "the customer's later files are compared against it."
        )
    if standing.get("any_approved"):
        return (
            "This file was read before a mapping spec was approved for this customer, so it was "
            "not compared. Upload it again to compare it with the approved file."
        )
    return (
        "There is no approved file to compare this one against yet. Approve a mapping spec "
        "for this customer and the next file will be compared with it."
    )
