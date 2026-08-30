# Phase 1 gate report

- Status: `IN_PROGRESS`
- Date opened: 2026-08-15
- File Store -> Scanner framework slice: `PASS`
- Mapping Template/Preview framework slice: `PASS`
- Persistent Mapping approval/Audit framework slice: `PASS`
- Canonical Store -> Scan -> Mapping route slice: `PASS`
- Deterministic pending Long-candidate slice: `PASS`
- Pending-only Long persistence and Source Cell slice: `PASS`
- Offline assumed-Qwen Mapping-location candidate slice: `PASS`
- Korean synthetic OQC canonical Data Engine acceptance slice: `PASS`
- Mapping schema v2 complete source-role vocabulary slice: `PASS`
- Persistent canonical hierarchy/row-binding/Master Spec framework slice: `PASS`
- Approved-Master review and explicit data-status decision slice: `PASS`
- Korean local manual-intake UI/API slice: `PASS`
- Durable Receipt -> Mapping review/approved Preview UI slice: `PASS`
- Receipt-bound Mapping Draft/Reviewer/Admin UI slice: `PASS`
- Receipt-bound Long candidate/explicit confirmation UI slice: `PASS`
- Korean explicit data-status review/Admin decision UI slice: `PASS`
- Korean canonical hierarchy/row-binding/Master first-setup UI slice: `PASS`
- Windows personal extension package framework slice: `PASS`
- Scanner broken-reference text false-positive hardening: `PASS`
- Golden Workbook Gate: `BLOCKED_BY_INPUT`

## Current vertical slice

- Project-isolated Original File Store
- Deterministic Workbook Scanner
- Manual single-file route through the same store-to-scan pipeline
- Versioned, scoped Mapping Template and evidence-only Mapping Preview
- Scanner -> Mapping Preview synthetic XLSX integration
- Persistent Draft -> Reviewed -> Approved/Superseded Mapping workflow
- Canonical provenance-bound Store -> Scan -> Mapping application outcome
- Deterministic approved-binding -> pending Long candidate boundary
- Project-scoped pending Long persistence with exact Source Cell evidence
- Korean synthetic OQC -> Scanner -> offline review-only AI location hints
- Korean synthetic baseline/historical -> approved Mapping -> pending Long ->
  isolated SQLite restart/replay acceptance
- Schema-v2 identifiers and row/Spec source evidence with schema-v1 hash/replay
  compatibility
- Project-isolated canonical Model -> ModelPart -> InspectionItem identities,
  independent Supplier axis, reviewed numeric Master Spec revisions, and
  persistent exact row-binding catalogs
- Deterministic approved-Master review candidates, explicit Admin data-status
  decisions, and a both-result-and-measurement `VALID` selector
- Korean local file selection, bounded asynchronous preservation/scan,
  Receipt/hash/Sheet evidence, and explicit `MAPPING_REQUIRED` UI state
- Receipt-rooted restart-safe rescan, explicit Supplier scope, paged exact
  source-cell review, and approved Template full-role Preview in Korean
- Exact-cell role selection, server-generated first Draft, separate Reviewer
  decision, separate Admin approval, and fresh same-Receipt approved Preview
- Approved-Mapping replay into exact Long candidates, visible loadable/held
  groups, and separate confirmation that stores only `PENDING`/`HELD`
- Job-scoped inspection-result selection, read-only Master comparison evidence,
  and explicit trusted-Admin `VALID`/`SUSPECT`/`EXCLUDED` decisions
- Project-scoped Model/ModelPart/Item/Supplier setup, explicit disposition,
  numeric Master and Mapping-row Binding Draft/Review/Admin approval
- Deterministic Windows extension ZIP, verified atomic local lifecycle, and
  localhost single-instance launcher without persistent OS/Scheduler changes

## Executed framework evidence

- Compile, Ruff lint/format, strict Mypy, requirement integrity, single-head
  Migration check, and the current Windows test Gate pass.
- Test result: 159 passed; 149 stable current contract IDs; minimum count 159;
  static/runtime skip 0; xfail/xpass 0.
- Frontend result: TypeScript typecheck, 18/18 Vitest tests, and Vite production
  build pass.
- Gate detail: Ruff/format 95 files; strict Mypy 63 sources; requirement
  integrity `333 baseline + 13 amendments / 83 scope / 74 status`; migration
  graph `0001 -> 0005` with one head.
- File Store evidence covers source/copy/stored hashes, receipt metadata,
  extension/MIME/OOXML validation, configured size rejection with no partial
  residue, same-content blob reuse with separate receipts, project isolation,
  path traversal rejection, and raw preservation after scan failure.
- Scanner evidence covers every sheet state, hidden rows/columns, merged and
  repeated-header candidates, formulas and cached values, external/broken
  reference warnings, protected sheets, real macro-capable package handling
  without VBA loading or execution, image-location metadata without image
  analysis, bounded package expansion, and duplicate OOXML package-part
  rejection.
- File Store and Scanner accept both OPC workbook main-type forms: an exact
  `/xl/workbook.xml` override or an applicable `xml` extension default. Exact
  override wins; duplicate, conflicting, missing, and wrong effective
  declarations remain fail-closed.
- Manual route evidence covers successful Store -> Scan reuse, known and
  unexpected scanner failure after preservation, and distinct propagation of
  missing or integrity-failed stored blobs.
- Local UI/API evidence covers bounded upload staging, single parser worker,
  capacity rejection, same-project opaque reads, safe errors, lifecycle
  restart, static production serving, and terminal staging cleanup. Actual
  Chrome smoke selected the Korean baseline, reached `MAPPING_REQUIRED`, and
  displayed the unchanged source hash plus three Sheet structures without
  horizontal overflow or console errors; see
  `reports/acceptance/2026-08-15-local-intake-browser-smoke.md`.
- Mapping UI/API evidence covers an exact project/receipt/hash lookup after the
  process-local intake registry is gone, immutable stored-source rescan, fresh
  persistent catalog load, and approved exact Preview with AI call count zero.
  Missing or changed layouts remain `MAPPING_REQUIRED` and expose bounded
  Sheet/Cell raw, cached, formula, rendered-status, protection, hidden-structure,
  and Scanner-warning evidence. All v1/v2 identifier and inspection-row roles
  survive the HTTP/UI DTO. A supported schema-v2 form can now select exact
  roles and persist a server-validated first Draft, followed by distinct
  Reviewer and Admin actions with CAS and Audit. Approval reloads a fresh
  catalog and requires the same Receipt to become `PREVIEW_READY`. Later
  Mapping revisions remain unavailable, while explicit Long confirmation and
  data-status decisions are now separate downstream UI actions.
- Windows package evidence covers deterministic ZIP bytes, extension identity
  and contract majors, exact per-file SHA-256 inventory, tamper rejection,
  zero-write dry-run, disjoint code/data roots, atomic install/update rollback,
  post-commit cleanup reporting, and code-only removal with data preservation.
  The launcher is localhost-only and uses a named mutex/health check; negative
  tests confirm no registry, autostart, service, Scheduler, Outlook, or live AI
  integration. Scheduler compatibility/discovery remains explicitly unverified.
- The current `0.1.0` ZIP (`68` files, `335,298` bytes, SHA-256
  `E685E513864F4504B28D8C7033FCBF08B89D865F09EBA19CAEE423CDD8F3BDD3`)
  passed isolated fresh installation with real Python 3.12 provisioning,
  localhost health/readiness, Korean desktop rendering, console errors 0,
  actual three-sheet OQC preservation/scan, and a first project configuration
  write with all automatic/official effects false. See
  `reports/acceptance/2026-08-15-personal-v1-installed-smoke.md`. The previous
  update/data-preservation and 390px installed-browser evidence remains in
  `reports/acceptance/2026-08-15-windows-extension-installed-smoke.md`.
- Mapping evidence covers project/approved supplier scope, supplier source
  aliases, source-derived inspection-date effectivity, immutable in-memory
  revisions and explicit supersession decisions, supported schema versions,
  exact structural fingerprints, source workbook hashes and Scanner warnings,
  numeric/qualitative rows, variable and optional sample roles, and exact
  Sheet/Cell evidence. Worksheet and ChartSheet structures are fingerprinted
  without fabricating a used range for ChartSheets. Supplier
  result/specification remain source claims; official values, calculations,
  and system judgment remain disabled.
- A generated three-sheet XLSX passes Scanner -> Mapping Preview integration.
  It contains no user workbook bytes, names, hashes, or values.
- Mapping revisions now persist in SQLite as project-scoped histories,
  immutable payload revisions, and immutable supersession decisions. Trusted
  Actor commands enforce Reviewer/Admin review and Admin-only final approval,
  optimistic versions reject stale writes, and Mapping plus Audit changes
  commit or roll back together.
- Alembic `0002` upgrades an existing `0001` Audit database without losing its
  Audit rows. Fresh install, downgrade to `0001`, and re-upgrade are covered.
- Direct Alembic commands now fail closed unless a database URL is explicit;
  Bootstrap supplies and restores the intended local development URL.
- Canonical manual intake binds receipt, stored-source Scan, and ready Preview
  by exact hash, source name, size, project, supplier scope, and Scanner issues.
  Scan failure, Mapping hold, and unexpected Mapping defects remain distinct;
  every post-preservation path keeps the raw receipt.
- Canonical intake depends on the storage-neutral Mapping catalog protocol. A
  real SQLite Draft -> Review -> Admin approval, catalog load, database restart,
  explicit snapshot reload, and ready Preview path is covered end to end.
- Pure Long-candidate evidence covers exact approved/effective row bindings,
  numeric and qualitative modes, variable or explicitly zero samples,
  deterministic replay, partial and global holds, identifier conflicts, and
  formula/cache/refresh cells. No unit conversion, standardized value,
  aggregation, `VALID` state, or system judgment is created.
- Alembic `0003` adds project-scoped source files, source-sheet snapshots,
  ingestion jobs, pending OQC lots, inspection-result candidates, and exact
  measurement-cell evidence. Composite foreign keys isolate projects and
  database checks permit only `PENDING`/`HELD`, null standardized values, and
  `NOT_EVALUATED` system judgment.
- Long persistence rebuilds and compares the approved Mapping Preview before
  claim/materialization. It preserves formula/raw/cache/display evidence,
  Mapping revision, binding-selection signature, source hash, and complete
  held-row snapshots. Untrusted bindings retain evidence but cannot populate
  canonical model/part/item columns.
- Long source/job claim and candidate materialization use separate
  transactions. A fatal materialization rolls back lot/result/measurement rows
  and then records `FAILED`; exact replay does not duplicate rows. A second
  receipt may reuse only a terminal successful pending materialization, while
  a processing or failed owner yields `RECOVERY_REQUIRED`.
- The materialization identity includes the Scanner contract version. Identical
  bytes scanned under a new contract create a distinct owner/materialization;
  an exact replay of a stalled processing job resumes the same job without
  duplicate rows.
- Migration evidence covers fresh `0003`, `0002 -> 0003`, downgrade/re-upgrade,
  and preservation of existing Audit and Mapping records using temporary
  databases only. The workspace development database was not used by these
  tests.
- Five Korean synthetic OQC workbooks cover a baseline, same-format historical
  lot, changed layout, ambiguous two-report structure, and explicit error
  conditions. Final Scanner before/after hashes are identical and every file
  remains non-Golden Framework evidence.
- The supported baseline maps six identifiers, six inspection rows, and 48
  exact sample cells. A distinct same-layout historical workbook reuses the
  persisted approved Mapping revision while retaining its own receipt, hash,
  LOT, inspection date, and raw values. Changed, ambiguous, and error forms
  preserve their raw bytes and Scanner evidence but remain
  `MAPPING_REQUIRED`; the prior Template is never forced.
- The baseline produces six `LOADABLE_PENDING` rows and 48 pending
  measurements. No unit conversion, standardized value, official calculation,
  Spec evaluation, or system judgment is created. An isolated temporary SQLite
  database persists them, restarts, and replays the exact receipt/job without
  duplicate lot, result, or measurement rows.
- Mapping schema v2 adds exact source roles for part name, production date,
  current/cumulative shipment quantity, section/category, unit, measurement
  point/location/cavity, Target, LSL, USL, and source Spec revision. These roles
  survive Preview, pending Long JSON, row/identifier evidence, SQLite restart,
  and exact replay without unit conversion, shipment arithmetic, Spec
  evaluation, or system judgment.
- Schema-v1 Mapping payload, Long snapshot, row-evidence shape, fixed SHA-256,
  and persisted replay remain unchanged. V1 carrying v2 evidence, schema-column
  swaps, missing/unknown/wrong-type/invalid-address v2 payload roles, and
  digest tampering fail closed. Mapping schema v2 itself required no migration
  because these are versioned JSON evidence fields; the later Master framework
  independently advances the migration graph to `0004`.
- Alembic `0004` persists project-isolated canonical Model, ModelPart,
  InspectionItem, and independent Supplier identities; immutable numeric
  Master Spec histories/revisions/supersessions; and exact supplier-scoped
  canonical row-binding histories/revisions/supersessions. All hierarchy,
  Mapping scope, and Supplier references use fail-closed composite keys.
- Inspection items start as `CANDIDATE` and require an audited Admin decision
  to become `MANAGED` or `EXCLUDED`. A candidate cannot receive an approved
  Master or row binding. An excluded item may retain an exact source binding
  without entering official analysis.
- Numeric Master revisions store exact finite Decimal text, an optional Target
  with one- or two-sided limits, unit, external revision label, immutable declared
  effectivity, reason/source, separate Reviewer/Admin decisions, and a resolved
  supersession end. Supplier OQC Spec evidence has no auto-copy path into the
  Master store.
- Persistent row bindings retain exact project/supplier/template/revision/row
  scope, source model aliases, sample policy, measurement mode, canonical
  hierarchy keys, revision workflow, and declared/resolved effectivity. Only
  one approved/effective revision per exact key is returned by an as-of catalog;
  later approvals require an explicit snapshot reload.
- Master tests cover stale CAS, cross-project/cross-Supplier/cross-Mapping FK
  rejection, payload digest tampering, interval overlap, invalid/target-only
  numeric limits, and Audit failure during multi-row Spec and Binding
  supersession. Mutation, resolved periods, successor status, supersession
  record, version counters, and Audit all roll back together.
- Migration evidence covers fresh `0004`, `0003 -> 0004`, downgrade to `0003`,
  re-upgrade, and Alembic metadata parity. Existing Audit, Mapping, source,
  sheet, job, lot, inspection-result, and measurement IDs/status/raw/SHA/version
  snapshots remain exact. Every migration test uses an explicit temporary URL;
  the workspace database was not used or upgraded by this slice.
- Data review evidence covers read-only candidate generation; exact historical
  approved-Master selection; inclusive one/two-sided comparison; `FAIL + VALID`
  separation; no automatic promotion; HELD/CANDIDATE/unit/integrity holds;
  same-Lot item isolation; Admin-only atomic result/measurement transition;
  CAS, restart idempotency, coordinated tamper rejection, and exact Audit.
- Alembic `0005` is a bounded SQLite migration. It preserves prior rows through
  fresh/upgrade/downgrade/re-upgrade tests, rolls a corrupt upgrade back with no
  backup artifacts or FK violations, and refuses a history-losing terminal
  downgrade before DDL. All migration tests use explicit temporary URLs; the
  default workspace database is not accessed by this slice.
- Scanner broken-reference classification now distinguishes formula/error/cache
  `#REF!` evidence from ordinary explanatory text. Real errors still produce
  exact-cell `BROKEN_CELL_REFERENCE` plus
  `CALCULATION_REFRESH_REQUIRED`; a plain text mention produces neither.
- The baseline and changed real Scanner outputs pass a provider-neutral offline
  AI exchange using exact Korean Sheet/Cell text and bounded scalar/date
  evidence. Every accepted result is a `SOURCE_LOCATION_HINT_ONLY` with
  `REVIEW_REQUIRED`; approval, persistence, calculations, and official values
  remain false.
- Strict JSON, duplicate keys, unknown fields, unsupported schema, stale request
  digests, missing/duplicate/fabricated source positions, forbidden official
  actions, and oversized output fail closed. Prompt-like Cell text prevents the
  provider call entirely. Disabled, timeout, expected, and unexpected provider
  failures leave the manual and approved-Mapping Core paths available.
- Qwen3.5-33B is recorded only as `runtime_verified=false`; there is no live
  adapter, endpoint, API key, credential reference, HTTP route, DB table,
  Worker, UI, or Scheduler access in this slice.
- Lock SHA-256:
  - `runtime.lock`: `006CD92B10A240FEC23456717295E0083CB8E93DD9A10E7F61FC2654BA6801F8`
  - `dev.lock`: `D8DAA7A087CFDBCEF89578E01BFB832A408C3B99815D94CE84397D0F55D2A7FB`
  - `package-lock.json`: `F248E113DF3055CFFBFAF807297F6F92AB4BD7D1385CA94099E22546165E7493`

The only observed non-blocking warning is the upstream FastAPI TestClient
transition warning from `httpx` to `httpx2`; it does not affect runtime code.

## Development database incident

A read-only migration audit accidentally rebuilt the default workspace
development database after its temporary URL setup failed. Post-incident
inspection found a valid `0002` schema with zero business/Audit rows and no
recoverable backup. The application had no business write route and all prior
persistence tests used temporary databases, so available evidence indicates it
was bootstrap-only and empty; absence of a pre-operation snapshot prevents a
proof. The full record and fail-closed regression are in
`reports/incidents/2026-08-15-default-dev-db-migration-audit.md`.

After `0003` passed independent review, the known post-incident `0002` database
was copied to a hash-identical 86,016-byte backup and upgraded through the
hardened explicit-URL Bootstrap. Read-only post-checks show head `0003`,
`quick_check=ok`, and zero prior/new table rows. This controlled backup does not
change the unresolved question about any hypothetical pre-incident data.

## Requirement disposition

- Implemented across the passed Phase 1 slices: `ARC-017`, `ARC-018`, `ARC-019`, `ING-001`,
  `ING-032`, `ING-037`, `ING-046`, `ING-047`, `ING-052`, `ING-053`,
  `ARC-029`, and `ARC-030`.
- Partial only: `ARC-007`, `ARC-008`, `ING-002`, `ING-020`~`ING-025`,
  `ING-028`, `ING-029`, `ING-031`, `ING-034`, `ING-035`, `ING-038`,
  `ING-044`, `ING-045`, `ING-049`, `ARC-022`, `ARC-023`, `ARC-025`,
  `ARC-026`, `ARC-028`, `CFG-001`,
  `CFG-002`~`CFG-007`, `CFG-009`, `CFG-016`, `CFG-017`, and `ING-041`.
- Phase 4 foundation only: `ANA-011` has a both-result-and-measurement
  `VALID` selector, but no Control Limit or analytical calculation.
- Acceptance blocked by real input: `GOV-013` and `ING-051`.
- Mapping/AI/data-review framework only, all still `IN_PROGRESS`: `GOV-004`, `GOV-005`,
  `GOV-007`, `GOV-008`, `ARC-014`~`ARC-016`, `CFG-004`, `CFG-017`,
  `ING-008`~`ING-012`, `ING-015`, `ING-018`, `ING-019`, `ING-024`, and
  `ING-025`.
- A bounded local Phase 1 intake/Mapping/Long/data-review API, Korean UI, and single-process parser
  worker now exist. There is still no durable cross-process Worker, Scheduler
  adapter, live AI provider, or analytical calculation. The screen stops at
  explicit source-role decisions, canonical hierarchy/first Master/Binding
  setup, pending/held Long confirmation, and explicit data-trust decisions.
  Later Mapping/Master/Binding revisions and supersession still lack a
  user-facing flow. Actual company Master values and Golden acceptance remain
  absent.

## Korean synthetic OQC and assumed-Qwen evidence

- Evidence record:
  `docs/references/2026-08-15-korean-oqc-ai-mapping-samples.md`.
- Output directory: `outputs/qwen_mapping_oqc_samples_ko_20260815/`.
- Final SHA-256 values:
  - `01_기준_한글_OQC_성적서.xlsx`:
    `782283EE4F60BD005F47A1D153846C632B35F21B2D5ED017D604315255FEBEBF`
  - `02_정상과거_한글_OQC_성적서.xlsx`:
    `DE7BBEFC67957E55322D6184CD967271FD35FC7BC0AF0EE65AC77717BD1449CB`
  - `03_양식변경_한글_OQC_성적서.xlsx`:
    `6BB8B98B54BF914268228F8EFB9E022696D7ADA5A39A61920D5C795F250AE3EB`
  - `04_애매구조_한글_OQC_성적서.xlsx`:
    `7CB07267BD0DE98755F5F933E314E83CF80C5C1950C8430C98674506B878EA80`
  - `05_오류포함_한글_OQC_성적서.xlsx`:
    `42F5D0DF5672CD157811DE1E7D1F71B5890B74BEDDFD2199EA53E794EDC1FA35`
- All 11 visible sheets passed full-resolution metadata-based visual QA with
  zero clipping, overlap, missing layout, or broken Korean glyphs. Excel itself
  was blocked by an Office licensing/sign-in screen and LibreOffice was absent;
  no bypass was attempted, so actual Excel print scaling remains unverified.

## User-provided demo workbook evidence

- `MASS_PRODUCTION_QUALITY_VALIDATION_OQC_Demo.xlsx`
- SHA-256:
  `E516A88B4D450EA9499C2D23BA0491AD276AF4286B45FFEAE9547EB4B43B9AEA`
- Read-only File Store and Scanner checks passed with unchanged before/after
  hashes. Scanner result: three visible worksheets and no formulas, external
  links, protection, or images.
- An injected temporary Template produced `PREVIEW_READY` for source-bound
  identifiers plus one qualitative and one numeric row, each with eight sample
  cells. The Preview retained `DISPLAY_VALUE_NOT_RENDERED`, the source hash,
  and `NOT_EVALUATED` system judgment.
- The workbook's own `Reference` sheet classifies it as a synthetic demo, not
  an original supplier report. It is Framework evidence only and cannot close
  the Golden Gate.
- The spreadsheet artifact renderer was unavailable in this session. Visual
  workbook acceptance was not claimed; read-only Mass Production Quality Validation scanning and bounded
  OOXML metadata inspection were used.

## Current evidence boundary

- Synthetic workbooks may verify structure, failure handling, and framework
  contracts only.
- The assumed-Qwen offline contract proves schema and failure isolation, not
  live model quality. Candidate confidence is display evidence only and never
  an auto-approval threshold.
- `ARC-008`, `ING-002`, `ING-029`, `ING-031`, `ING-034`, `ING-035`, and
  `ING-049` remain partial at this slice.
- `GOV-013` and `ING-051` remain `BLOCKED_BY_INPUT`.
- The file-store lock is process-local. This is correct for the current single
  local process; durable cross-process claiming/locking is required before a
  multi-process Worker or server deployment.
- The supported persistent command path implements Draft -> Reviewed ->
  Approved/Superseded transitions, role checks, optimistic locking, and Audit.
  Existing direct `APPROVED` dataclass construction remains only as an
  in-memory framework/fixture compatibility path and is not an authorization
  workflow. The local UI now invokes the first-revision Draft, Reviewer, and
  Admin commands without accepting actor data from HTTP bodies. Production
  authentication, later revision editing, retirement, and supersession UI
  remain future work.
- The current Template model is a bounded row-oriented Cell mapping. Shared
  merged anchors, reusable common Spec/method cells, Cell ranges, and
  column-oriented supplier formats require later Mapping model extensions.
- `project_key` is an upstream routing precondition. Mapping Preview verifies
  project scope but does not infer a project from model/part evidence by itself.
- Same-content manual intake intentionally creates a distinct Receipt while
  reusing one project-local Blob. Long persistence creates a distinct source
  and job history and may reference a prior terminal successful pending
  materialization. Processing/failed owners require explicit recovery; there
  is no automatic Worker retry or cross-process File Store lock.
- A persistent Mapping catalog is a materialized immutable snapshot. It remains
  usable after its DB session closes but does not observe later approvals; a
  route with a longer lifetime must explicitly reload and reinject it.
- Long ingestion remains an auditable staging boundary and creates only
  `PENDING`/`HELD`. A separate explicit Admin decision may move an eligible
  result and all measurements to `VALID`, `SUSPECT`, or `EXCLUDED` while
  preserving the applied Master and Audit. It never runs automatically.
- `REPLACED` is reserved in the state vocabulary, but no result transition or
  replacement-chain command exists in this slice; `ING-042` remains deferred.
- `0003` is verified for SQLite only. PostgreSQL support requires a dedicated
  migration for the partial owner index and dialect-portable Boolean checks;
  no PostgreSQL compatibility claim is made by this Gate.

## Golden Gate still required

- Representative OQC workbook
- Two or three files of the same form
- Applicable Master Spec or approved acceptance criteria
- User review of 100% identifier, Spec, shipment, raw-measurement, and source
  Sheet/Cell comparison with zero unexplained mismatch

This report cannot become `PASS` from Synthetic Fixture results alone.
