# Cassettes

Test fixtures, deliberately under `tests/` rather than `fixtures/`: that directory is
the synthetic-data generator's, and `speedy-generate --verify` fails the build on anything
there it did not produce - correctly, since a hand-edited generator fixture would silently
change what three later suites are scored against.

Recorded-shaped model outputs, used so the proposal pipeline can be tested for free and
deterministically.

## `mapping_proposal_csv.json` is **hand-authored, not recorded**

No live call has been made: `OPENAI_API_KEY` is unset and the model id is unconfirmed, so
there was nothing to record. This file is a plausible response written by hand against the
strict-mode schema.

**What that means for what the tests prove.** The test that scores this cassette at
≥75% against the generator's oracle is a test of **the scoring harness and the guard** —
that a proposal in this shape is parsed, validated against the real column list, scored
the same way the naive matcher was scored in P0, and compared against the right threshold.
It is **not** evidence that the model maps this file well. That claim needs a live call,
and the same assertion is written to run against one (`-m llm`) when a key is configured.

The distinction matters because the whole premise of Phase 6 is a *measured* improvement
over name matching. Measuring it against an answer sheet I wrote myself would be marking
my own homework, which is exactly what P0's naive-matcher baseline was kept in the
tester's hands to avoid.

The cassette deliberately contains one wrong column choice (`invoice_vendor` pointed at
`CLSCD1`, the brand code, rather than `REMTNO`, the remit-to vendor) so that the harness is
exercised on a miss as well as on hits, and the reported score is not a flat 100%.
