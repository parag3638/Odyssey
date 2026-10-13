# Canonical invoice line — file format

**`canonicalSchemaVersion`: 1.0.0**

This document describes the **CSV file**. `invoice_line.schema.json` describes the
**object**. They are deliberately two documents: a JSON Schema has nothing to say about
header text, quoting or line endings, and a CSV has nowhere to put a type. Both are
generated from or checked against `src/features/canonical/model.py`, which is the single
source of truth — `make check` fails if the schema file has drifted from it.

> **Standing in for something unpublished.** Speedy Labs' real intake format is not
> public. This format is what their documented pipeline implies — one row per invoice
> line, money as exact decimals, a closed UOM set — and is deliberately plain so that
> swapping it for the real one is a change to this file and the writer, not to the
> mapping, validation or variance logic. The README lists it as an integration point.

## The file

| Property | Value |
|---|---|
| Media type | `text/csv` |
| Character encoding | UTF-8, no BOM |
| Line terminator | `\n` (LF), including after the final row |
| Header row | Required, exactly once, as the first line |
| Field separator | `,` |
| Quote character | `"` |
| Quoting | Minimal — a field is quoted only if it contains `,`, `"`, `\n` or `\r` |
| Escaping | A `"` inside a quoted field is doubled (`""`), per RFC 4180 |
| Null representation | An empty field (zero characters between separators) |
| Trailing separator | None |
| Row order | Not significant; the writer emits source order |

An empty field means **absent**. There is no sentinel: not `NULL`, not `\N`, not `-`.
A field whose value is genuinely the empty string cannot occur, because no canonical
field admits one — every text field that is present has at least one character.

## Columns, in order

The header text is exactly the field name, lower snake case, as listed here. This order
is also published as `x-csvColumnOrder` in the JSON Schema.

| # | Column | Type | Required | Notes |
|---|---|---|---|---|
| 1 | `invoice_no` | text | yes | As printed on the invoice |
| 2 | `invoice_date` | date | yes | `YYYY-MM-DD` |
| 3 | `customer_id` | text | yes | **Bill-to** account, not ship-to |
| 4 | `customer_name` | text | yes | |
| 5 | `dist_item_id` | text | yes | Distributor's item number |
| 6 | `mfr_item_code` | text | yes | Manufacturer's item or part code |
| 7 | `brand_manufacturer` | text | yes | Whose brand the item carries |
| 8 | `invoice_vendor` | text | yes | Who was bought from; differs under a redistributor |
| 9 | `qty` | decimal(3) | yes | In the line's unit of measure |
| 10 | `uom` | enum | yes | One of `CS`, `EA`, `LB` |
| 11 | `pack_size` | integer | no | Eaches per case; empty when not supplied |
| 12 | `catch_weight_lbs` | decimal(3) | no | Pounds; empty for non-catch-weight items |
| 13 | `unit_price` | decimal(4) | yes | Per unit of measure |
| 14 | `ext_amount` | decimal(2) | yes | Should equal `qty` × `unit_price` within tolerance |
| 15 | `is_deviated` | boolean | yes | `true` or `false`, lower case |
| 16 | `deviated_price` | decimal(4) | no | Empty unless `is_deviated` is `true` |

## Numbers

Every numeric field is written as a **plain decimal string**. No thousands separators, no
currency symbol, no exponent, no parentheses for negatives. A leading `-` marks a
negative. `decimal(n)` means at most `n` fractional digits; fewer is fine, and a trailing
`.00` is neither required nor forbidden.

This is the one rule worth stating twice: these are decimals, not floats, at every stage.
A rebate computed from a float is wrong by fractions of a cent per line and by real money
across 50,000 lines, and the error is invisible until a customer reconciles it.

The writer emits each numeric field at **exactly** the scale listed above — `9.000`, not
`9`, for a quantity. Reading it back is unaffected (a reader parses a decimal), but a
diff of two exports is then a diff of values rather than of formatting.

There is no currency column in version 1.0.0 — the demo is single-currency (USD). Adding
one is additive, and would arrive as `1.1.0`.

## Text fields and spreadsheet formulas

A **text** field whose first character is `=`, `+`, `-`, `@`, a tab or a carriage return is
written with a leading apostrophe (`'`). Without it, opening the file in Excel, Sheets or
Numbers executes the cell: a `customer_name` of `=HYPERLINK("http://evil","Click")` becomes
a live link, and `=cmd|'/c calc'!A0` is worse. The apostrophe is the spreadsheet convention
for "this is text", and a consumer that is not a spreadsheet should strip one leading
apostrophe from a text field.

Numeric, date and boolean fields are **not** prefixed: they are written from typed values,
so they cannot contain a formula, and prefixing them would turn a negative amount into a
string and break the decimal rule above.

## Booleans

`is_deviated` is written `true` or `false`, lower case, unquoted. Not `Y`/`N`, not `1`/`0`.
A distributor's own flag (`Y`, `D`, `*`) is mapped onto it by a `map_values` transform at
mapping time, so the canonical file never carries a source convention.

## Versioning

`canonicalSchemaVersion` is semantic:

- **patch** — documentation only; no field, type or representation change.
- **minor** — a new optional column appended, or a widened enum. A reader of `1.0.0` can
  still read `1.1.0` by ignoring unknown trailing columns.
- **major** — a column renamed, removed, retyped, or reordered. Every stored mapping spec
  names the version it was written against, so an old spec is never silently reinterpreted
  under new rules.

Changes are additive (`BACKEND.md` §3). A column is never renamed in place.

## Example

```csv
invoice_no,invoice_date,customer_id,customer_name,dist_item_id,mfr_item_code,brand_manufacturer,invoice_vendor,qty,uom,pack_size,catch_weight_lbs,unit_price,ext_amount,is_deviated,deviated_price
HP26000001,2026-01-01,200013,Cedar Grove Dining Group,100700,CPC-3559,Crosspoint Creamery,Crosspoint Creamery,9.000,CS,6,,18.4100,165.69,false,
HP26000002,2026-01-02,200104,"Havenwood Hotel Group, Inc.",500220,NVF-8589,Northvale Foods,Bayline Creamery,3.000,LB,,53.100,19.1600,57.48,true,17.9500
```

Row 1 is a case-priced line with no catch weight and no deviation. Row 2 is a
catch-weight pound line bought through a redistributor at a deviated price, with a
quoted customer name because it contains a comma.
