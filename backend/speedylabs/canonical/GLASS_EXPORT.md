# Attributed variance export

A documented, versioned JSON of attributed rebate variances with their source lines.

**This is a proposed shape, not a contract.** The spec asks for results "in a format that could
feed" a downstream root-cause tool. We do not know that tool's real interface and have not agreed
one with anyone, so what is published here is a plausible shape with a committed JSON Schema — the
point being that adapting it to a real integration is a mapping exercise rather than reverse
engineering. Nothing here should be read as an agreed interface.

## Why JSON and not CSV

A variance carries a nested list of the source lines that explain it, and that nesting is the one
thing a downstream tool could not recompute for itself — it is the whole value of the export. A CSV
would either flatten it (losing the grouping) or need two files (losing the join).

## Shape

- `glassExportVersion` — bumped when the shape changes. Consumers pin it.
- Run metadata: `customerName`, `ingestRunId`, `legacySourceFile`, the period range,
  `canonicalSchemaVersion`, and `engine` — which carries `standIn: true`, because every amount in
  this export was computed by a reference stand-in and not by Speedy's rebate engine.
- Headline: `explainedPct`, `totalVarianceAmount` (absolute), `netVarianceAmount` (signed),
  `unexplainedAmount`.
- `variances[]` — one per program per period, with `byCause`, `residueAmount`, and `sourceLines[]`
  carrying the invoice, item, source row, contribution and the evidence sentence.

## Two things a consumer must know

**`netVarianceAmount` and `totalVarianceAmount` differ, and the gap is not an error.** One cause,
`date_cutoff`, *relocates* money between periods rather than creating it: the same dollars leave one
month and arrive in the next, so they cancel in any net. On the seeded demo the net is `4,035.14`
while the absolute total is `9,539.64`. `explainedPct` is computed over the **absolute** figure,
because a net denominator understates the work and can be `0/0`.

**Amounts are decimal strings, and some are estimates.** A source line with `"estimated": true`
carries an amount derived from other lines — the catch-weight cause, where the weight was stripped
from the customer's export and cannot be recovered. The evidence sentence says so per line.

All money is a decimal string. Never parse it as a float.
