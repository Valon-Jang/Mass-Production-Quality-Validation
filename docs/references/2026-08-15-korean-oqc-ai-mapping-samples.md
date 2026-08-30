# Korean synthetic OQC samples and offline AI Mapping evidence

- Date: 2026-08-15
- Evidence class: `SYNTHETIC_FRAMEWORK_ONLY`
- Golden evidence: `false`
- Assumed model contract: `Qwen3.5-33B`, `runtime_verified=false`
- Output directory: `outputs/qwen_mapping_oqc_samples_ko_20260815/`

## Purpose and boundary

The five workbooks provide Korean OQC-shaped inputs for repeatable Scanner and
review-only AI Mapping contract tests when no representative supplier workbook
or live cloud model is available. All suppliers, models, parts, lots, people,
dates, quantities, measurements, and criteria are fictional. The files do not
replace a user-approved Golden Workbook, Master Spec, or real Qwen acceptance.

The spreadsheet artifact runtime was unavailable, so the user explicitly
approved the `openpyxl` fallback. The reproducible builder is
`scripts/build_korean_oqc_samples.py`. It does not read or copy the immutable
planning ZIP, the user demo workbook, mail, Scheduler state, secrets, or an
external API.

## Final outputs

| Workbook | Scenario | Bytes | SHA-256 |
|---|---|---:|---|
| `01_기준_한글_OQC_성적서.xlsx` | Baseline Korean OQC form | 13,051 | `782283EE4F60BD005F47A1D153846C632B35F21B2D5ED017D604315255FEBEBF` |
| `02_정상과거_한글_OQC_성적서.xlsx` | Same structure, earlier lot and values | 13,057 | `DE7BBEFC67957E55322D6184CD967271FD35FC7BC0AF0EE65AC77717BD1449CB` |
| `03_양식변경_한글_OQC_성적서.xlsx` | Moved identifiers, changed table, new item | 13,845 | `6BB8B98B54BF914268228F8EFB9E022696D7ADA5A39A61920D5C795F250AE3EB` |
| `04_애매구조_한글_OQC_성적서.xlsx` | Two report sheets, conflicting model/lot context | 9,860 | `7CB07267BD0DE98755F5F933E314E83CF80C5C1950C8430C98674506B878EA80` |
| `05_오류포함_한글_OQC_성적서.xlsx` | Missing lot, bad date, formula/reference, hidden/protected and injection cases | 13,430 | `42F5D0DF5672CD157811DE1E7D1F71B5890B74BEDDFD2199EA53E794EDC1FA35` |

Every workbook contains a visible `합성자료안내` sheet that states its
fictional status and expected Mass Production Quality Validation behavior. Raw sheets are hidden only to
exercise discovery; the Scanner still includes them. The error workbook's
prompt-like text is test data and never an instruction.

## Verification evidence

- The five files were regenerated after the spreadsheet-operation start record
  and rehashed as the final outputs above.
- `OpenpyxlWorkbookScanner` read every final scenario without modifying it;
  each Scanner before/after SHA-256 matched and every scan reported
  `is_golden_workbook_evidence=false`.
- Baseline and historical reports retained the same used range, merges, and row
  signatures. The changed report produced different structure evidence. The
  ambiguous workbook retained both model contexts.
- The error workbook produced exact `BROKEN_CELL_REFERENCE` at `P10` and
  `EXTERNAL_REFERENCE_FORMULA` at `P11`, retained protected/hidden metadata,
  and caused prompt-injection detection before any provider call.
- The real Scanner output from the baseline and changed workbooks passed the
  offline AI exchange with exact existing Korean Sheet/Cell tokens and only
  `REVIEW_REQUIRED` source-location hints. No approval, persistence,
  calculation, or official value was created.
- The baseline also passed the canonical non-AI Data Engine path with one
  persisted Draft -> Reviewed -> Approved Mapping revision: six supported
  identifiers, six inspection rows, and 48 exact sample cells reached a
  pending Long candidate. The distinct historical workbook reused that exact
  Mapping revision while retaining its own receipt, hash, LOT, date, and raw
  measurements.
- The changed form, ambiguous two-context form, and error form all preserved
  their original bytes and complete Scanner evidence but returned
  `MAPPING_REQUIRED`; no old Mapping, Long candidate, or database write was
  forced. The error evidence includes exact broken/external formula cells,
  sheet protection, a hidden row/column, an invalid date, and injection text.
- An isolated temporary SQLite database persisted one pending lot, six pending
  inspection rows, and 48 measurements. After database restart, exact replay
  reused the original receipt-scoped job and created no duplicate rows.
- All 11 visible sheets were rendered from workbook style/merge/size metadata
  and inspected at full resolution after the final revision: zero clipping,
  overlap, missing layout, or broken Korean glyphs. The changed form's sample
  count remains integer `8` with `General` number format.

Excel itself was installed but blocked by an Office licensing/sign-in screen;
LibreOffice was not installed. No sign-in or license bypass was attempted.
Consequently, the actual Excel print engine and A4 one-page scaling remain
unverified. Landscape, print area, and one-page-width metadata are present, but
18-19-column reports may be small on A4 and should receive A3 or two-page print
acceptance if physical printing becomes a requirement.

## Acceptance still blocked

- Actual cloud `Qwen3.5-33B` endpoint compatibility, accuracy, latency, and
  token behavior
- Shared Scheduler profile and current-user Secret Store handoff
- Representative supplier OQC plus two or three same-form historical files
- Approved Master Spec or acceptance criteria
- User comparison and sign-off with zero unexplained mismatch

Therefore `ARC-027` remains `DEFERRED_BY_PHASE`, while `GOV-013` and `ING-051`
remain `BLOCKED_BY_INPUT`.
