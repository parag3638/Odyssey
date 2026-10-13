"""Typed application errors and the one shape they serialise to.

BACKEND.md §3 fixes the error envelope; §9 says services raise typed errors and a single
handler turns them into that shape. Nothing here knows about HTTP objects — `status` is a
plain int so `main.py` can map it without importing FastAPI into service code.
"""

from __future__ import annotations


class AppError(Exception):
    """Base for every error we deliberately return to a caller.

    `message` is user-facing and follows DESIGN.md §9: what happened and how to fix it,
    no apology. It is safe to show in a toast as-is, so it must never carry a stack
    trace, SQL, a file path or an internal name.
    """

    status: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        fields: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.fields = fields
        # Some statuses are defined partly by a header, not only a body. §3's table
        # requires `Retry-After` on a 429, and a message that says "try again in about
        # 40 minutes" is not a substitute: a client retrying programmatically reads the
        # header, not the prose.
        self.headers = headers or {}


class NotFound(AppError):
    """Missing, or not the caller's. Both return 404 so neither leaks the difference."""

    status = 404
    code = "not_found"


class Forbidden(AppError):
    status = 403
    code = "forbidden"


class Conflict(AppError):
    status = 409
    code = "conflict"


class RuleBroken(AppError):
    """Valid shape, breaks a business rule (BACKEND.md §3, 422)."""

    status = 422
    code = "rule_broken"


class ProfileNotReady(AppError):
    """The resource will exist, just not yet.

    409, not 404 and not 500: a 404 tells the caller to stop asking, and a 500 blames
    the server for the caller being early.
    """

    status = 409
    code = "profile_not_ready"


class MalformedRequest(AppError):
    """The request could not be understood - a bad cursor, an unparseable parameter.

    400 rather than 422: 422 means "well-formed but breaks a rule", and this did not
    parse at all.
    """

    status = 400
    code = "malformed_request"


class UnsupportedFileType(AppError):
    """The bytes are not a plain-text export. 422: the request is well-formed, the
    content breaks a rule."""

    status = 422
    code = "unsupported_file_type"


class TooManyRows(AppError):
    """Past `MAX_UPLOAD_ROWS`. Detected while streaming, not after loading."""

    status = 422
    code = "file_too_many_rows"


class Unparseable(AppError):
    """The file is text but not a file we can read. Names the offending line."""

    status = 422
    code = "file_unparseable"


class TooLarge(AppError):
    """Body or upload over its cap. Deviation 1: §3's table has no 413 row; proposed."""

    status = 413
    code = "too_large"


class InvalidLayout(AppError):
    """A proposed field layout does not describe a readable record.

    422: the request is well-formed, the layout breaks a rule. `fields` is keyed by
    field index so the editor can put each message under the input that caused it.
    """

    status = 422
    code = "invalid_layout"


class LayoutLocked(AppError):
    """Canonical lines already exist under this layout, so its boundaries are load-bearing.

    409: changing where a field starts, after rows have been materialised from it, would
    leave those rows describing a layout that no longer exists - silently, because
    nothing recomputes them. The caller is told to start a new run instead.
    """

    status = 409
    code = "layout_locked"


class InvalidTransform(AppError):
    """A transform instruction is not a member of the closed set, or its parameters are
    out of range.

    422. Its own code because the mapping UI shows it against the transform picker, and
    because from P6 it is also the answer to "the model proposed something that is not a
    transform" - a case that must be distinguishable from a human's mistake.
    """

    status = 422
    code = "invalid_transform"


class MappingIncomplete(AppError):
    """Validation was asked for while a required canonical field has no source column.

    422 with `fields` keyed by canonical field name, so the mapping UI can mark each
    unmapped required row rather than showing one banner the user has to decode.
    """

    status = 422
    code = "mapping_incomplete"


class UnknownCanonicalField(AppError):
    """A mapping names a canonical field the committed schema does not have.

    422 with its own code: the mapping UI marks the row, and from P6 it is also how a
    model's invented field name is reported - which must be distinguishable from a
    human's typo in the logs.
    """

    status = 422
    code = "unknown_canonical_field"


class UnknownSourceColumn(AppError):
    """A mapping names a source column this file's profile does not contain."""

    status = 422
    code = "unknown_source_column"


class MappingLocked(AppError):
    """Canonical lines already exist, so the mapping that produced them is fixed.

    The mapping decides what every stored value *means*; changing it under materialised
    rows would leave them describing an interpretation nobody can reconstruct.
    """

    status = 409
    code = "mapping_locked"


class ValidationMissing(AppError):
    """A spec was approved from a draft nobody has validated.

    422 with its own code: the mapping screen turns it into one action - press Validate - and a
    generic `rule_broken` would make the client match on message text to know that.
    """

    status = 422
    code = "validation_missing"


class ValidationStale(AppError):
    """The stored verdicts describe a different mapping from the one being approved.

    The defect this closes: validate, edit one field, approve - and a mapping nobody checked
    becomes the document governing every future file from that customer.
    """

    status = 422
    code = "validation_stale"


class ValidationFailing(AppError):
    """A field's verdict is `fail` and the approval was not forced.

    `warn` does not block: it means the *data* has gaps. `fail` means the *mapping* is wrong
    (`docs/decisions.md`, 2026-10-06), and remembering a wrong mapping is worse than
    remembering none.
    """

    status = 422
    code = "validation_failing"


class SpecNoteRequired(AppError):
    """Forcing an approval over a failing verdict requires an explanation on the record."""

    status = 422
    code = "spec_note_required"


class SpecConflict(AppError):
    """Another approval for this customer and format won the race.

    409: the caller's request was valid and is simply no longer the newest. Retrying after
    reading the new version is the answer, which is what the message says.
    """

    status = 409
    code = "spec_conflict"


class DriftBlocked(AppError):
    """This file differs from the last approved one in a way that stops it being ingested.

    409: the run exists and the file is readable; a human has to accept the change or reject the
    file. Its own code because the output screen turns it into one specific action - open the drift
    comparison - and because "blocked" already means something else in this product (readiness found
    defects *after* ingesting).
    """

    status = 409
    code = "drift_blocked"


class FileRejected(AppError):
    """A human rejected this file. It is kept, and marked, rather than deleted.

    409 rather than 410: the record is still there and still readable, which is the point - a
    rejection is auditable and reversible by approving a new spec.
    """

    status = 409
    code = "file_rejected"


class NoteRequired(AppError):
    """A decision that changes how later files are read, with no explanation.

    422, not 400: the request is well-formed and breaks a rule, which is exactly §3's distinction.
    A note of whitespace is the case that forced its own code - `min_length=1` on the field lets
    `"   "` through, and the rule is about meaning rather than length.
    """

    status = 422
    code = "note_required"


class DriftAlreadyDecided(AppError):
    """Accept or reject was called on a run whose drift has already been decided."""

    status = 409
    code = "drift_already_decided"


class NoBaseline(AppError):
    """There is nothing to compare this file against."""

    status = 409
    code = "no_baseline"


class NotMaterialized(AppError):
    """Readiness was asked for before the canonical rows exist.

    409 rather than 404: the run exists and the checks will be possible the moment it is
    materialised, so telling the caller to stop asking would be wrong. Its own code because
    the readiness screen turns it into one specific action - materialise the run.
    """

    status = 409
    code = "not_materialized"


class ReadinessNotRun(AppError):
    """The checks have not been run against this run yet.

    409 for the same reason as `NotMaterialized`: a state that will pass, not a dead end.
    """

    status = 409
    code = "readiness_not_run"


class NoRebatePrograms(AppError):
    """A calculation was asked for before any rebate program exists for this customer.

    422 rather than 409: the request is well formed and the run is fine - a rule is unmet, which
    BACKEND.md §3 makes a 422. Its own code because "we calculated and the answer is $0.00" and
    "nobody has told us what to calculate" are different sentences, and only the second has a fix
    the user can act on.
    """

    status = 422
    code = "no_rebate_programs"


class RebatesNotCalculated(AppError):
    """Rebate results were asked for before the calculation ran.

    409 for the same reason as `NotMaterialized`: a state that will pass, not a dead end. **P12 and
    P13 reuse this code** rather than minting their own `not_calculated` - two codes meaning "the
    upstream computation has not happened" is how a client ends up handling only one of them.
    """

    status = 409
    code = "rebates_not_calculated"


class LegacyUnmapped(AppError):
    """A legacy rebate export whose columns are not the documented shape.

    422 rather than 400: the request is well formed and the file is readable text - a *rule* about
    its shape is unmet, which BACKEND.md §3 makes a 422.

    Its own code because this is the scope guard, and the message is the whole point: it names the
    three required columns and the two permitted ones, so the answer is "document the shape", never
    "teach the importer to sniff". This reader must not grow into a second parser for formats Speedy
    already handles natively.
    """

    status = 422
    code = "legacy_unmapped"


class LegacyUnmatched(AppError):
    """Not one legacy row matched a rebate program.

    422: the file imported fine, and the rule that fails is "there is something to compare". Its own
    code because the fix is specific - the program references in the export and the ones seeded
    against this customer disagree - and a generic refusal would send someone looking at the file.
    """

    status = 422
    code = "legacy_unmatched"


class LegacyNotImported(AppError):
    """Variances were asked for before a legacy export was imported.

    409 for the same reason as `NotMaterialized`: a state that will pass, not a dead end.
    """

    status = 409
    code = "legacy_not_imported"


class VariancesNotComputed(AppError):
    """Variance results were asked for before a comparison was run.

    409 for the reason `NotMaterialized` and `RebatesNotCalculated` are: a state that will pass, not
    a dead end. Its **own** code rather than reusing `legacy_not_imported`, which means something
    genuinely different - that the legacy export itself has not finished importing. One code for two
    unrelated states is how a client ends up handling neither properly. **P13 reads this code**, so
    it is defined once here.
    """

    status = 409
    code = "variances_not_computed"


class DemoResetDisabled(AppError):
    """Reset is off on this instance.

    403 rather than 404: the route exists and the refusal is a deliberate configuration, which is
    something an operator needs to be able to tell apart from a typo in the path.
    """

    status = 403
    code = "demo_reset_disabled"


class ResetInProgress(AppError):
    """A reset is already running. 409: a state that will pass, not a dead end."""

    status = 409
    code = "reset_in_progress"


class AlreadyMaterialized(AppError):
    """This run's canonical rows already exist.

    409 with its own code, not a bare `conflict`: the output screen distinguishes "you
    already did this" - which is reassuring and points at starting a new run - from any
    other conflict, and a client should not have to match on message text to tell them
    apart.
    """

    status = 409
    code = "already_materialized"


class NothingToDownload(AppError):
    """A download was asked for before anything was materialised.

    409 rather than 404: the run exists and the file will exist the moment it is
    materialised, so telling the caller to stop asking would be wrong.
    """

    status = 409
    code = "nothing_to_download"


class LlmProposalInvalid(AppError):
    """The model answered, but its answer could not be used as a mapping.

    422 rather than 503: the service is up and it replied. Separate from
    `DependencyUnavailable` because the advice differs - a truncated proposal means this
    file is too wide for one pass, which is a fact about the file, not an outage.
    """

    status = 422
    code = "llm_proposal_invalid"


class RateLimited(AppError):
    status = 429
    code = "rate_limited"


class SpendCapReached(AppError):
    """The LLM daily cap. 429 rather than 503: it is our limit, not a dependency fault."""

    status = 429
    code = "spend_cap_reached"


class DependencyUnavailable(AppError):
    status = 503
    code = "dependency_unavailable"
