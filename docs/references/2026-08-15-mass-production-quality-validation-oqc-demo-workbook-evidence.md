# Mass Production Quality Validation OQC demo workbook evidence

- Observed: 2026-08-15
- Source file: `MASS_PRODUCTION_QUALITY_VALIDATION_OQC_Demo.xlsx`
- Size: 17,351 bytes
- SHA-256: `E516A88B4D450EA9499C2D23BA0491AD276AF4286B45FFEAE9547EB4B43B9AEA`
- Classification: user-provided synthetic reference workbook

## Evidence boundary

The workbook's own `Reference` sheet describes it as a synthetic planning and
demo example, not an original supplier report. It is therefore useful
Framework evidence but is not an immutable planning baseline, a representative
supplier workbook, an approved Master Spec, Golden acceptance evidence, or a
source of official thresholds and judgments.

The workbook was opened read-only. Its SHA-256 was identical before and after
File Store, Scanner, and Mapping Preview checks. No workbook cell, style,
formula, property, sheet, or file timestamp was intentionally changed.

## Read-only structure evidence

- `OQC_Report`: `A1:R27`, one merged title range, report identifiers followed
  by a mixed qualitative/numeric inspection table and variable populated
  sample cells.
- `MASS_PRODUCTION_QUALITY_VALIDATION_Raw`: `A1:P140`, a synthetic expected long-form reference included
  by the workbook author. It is comparison data only and is not trusted as an
  official Mass Production Quality Validation result.
- `Reference`: `A1:B12`, provenance, intended demo anomalies, and usage notes.
- Scanner result: `SCANNED`, three visible worksheets, no formulas, no external
  links, no protection, and no image content.
- Display text remains explicitly `NOT_RENDERED`; the Scanner emitted
  `DISPLAY_VALUE_NOT_RENDERED` rather than fabricating Excel-rendered text.

The available spreadsheet artifact runtime was not exposed in this session,
so no visual renderer was used. Structure, values, number formats, package
metadata, and hashes were checked through the Mass Production Quality Validation read-only Scanner and
direct bounded OOXML metadata inspection.

## Defect discovered and fixed

The package resolves the workbook main content type through an OPC
`Default Extension="xml"` declaration rather than a part-specific
`/xl/workbook.xml` override. The initial File Store and Scanner accepted only
the common override form and rejected this workbook as corrupt.

Both readers now resolve an exact override first and, when absent, use the
applicable extension default. Duplicate, conflicting, missing, or wrong
effective declarations remain fail-closed. Synthetic regression tests protect
both accepted forms and all rejection cases.

## Mapping Preview evidence

A temporary approved in-memory template was injected for this demo structure;
no supplier cell address was placed in production code. The real Scanner
result then produced `PREVIEW_READY` for:

- supplier, model, part number, LOT, inspection date, and revision identifiers;
- one qualitative inspection row with eight source samples; and
- one numeric inspection row with eight source samples.

Every previewed value retained its worksheet and cell coordinate, the workbook
hash, raw/cached/formula/display-status evidence, and Scanner warnings. The
supplier-provided result remained separate, while Mass Production Quality Validation system judgment
stayed `NOT_EVALUATED`; no official value or calculation was created.

This bounded preview does not map or approve every report row, does not load
the included synthetic long table into a business database, and does not close
the Phase 1 Golden Gate.

