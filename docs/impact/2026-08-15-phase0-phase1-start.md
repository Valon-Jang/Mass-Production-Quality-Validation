# Impact map: Phase 0 and first Phase 1 slice

- Date: 2026-08-15
- Authorization: user explicitly approved implementation start in the current
  conversation.

## Requirements and sources

- Governance: GOV-001, GOV-002, GOV-003, GOV-007~012.
- Foundation: ARC-020 and Phase 0 Gate contract.
- First scanner slice: ARC-017~019, ING-001, ING-029, ING-032,
  ING-037~038, ING-046~047.
- Partial only: ARC-008, ING-002, ING-031, ING-034~035, ING-049.
- Scheduler: ARC-001~007, ING-003, ING-048 remain documentation/port work only
  and are not live integration deliverables.

## Files and consumers

- New repository commands, dependency locks, backend application, Alembic
  migration, tests, gate reports, and Living Context documents.
- SQLite is development-only. Tests use temporary databases and stores.
- There are no existing API/UI/worker consumers to migrate.

## Paths and failure modes

- Normal: clean bootstrap -> migration -> local health -> file store -> scan.
- Failure: invalid input is rejected without modifying the source; stored raw
  data survives scan failure; cache/derived work never rolls back raw storage.
- Restart/duplicate: a content hash reuses the project-local blob while every
  receipt remains an independent ingestion record. The current file-store
  lock is process-local, so this slice is explicitly limited to one local DQ
  NEXUS process. A durable cross-process claim/lock is required before any
  multi-process Worker or server deployment.
- Ambiguous or unsafe OOXML: duplicate package-part names, extension/main-type
  mismatch, encrypted/corrupt input, excessive part counts, and configured
  expanded-size limits are rejected before workbook interpretation.
- Offline: no Scheduler, Outlook, AI, or network dependency is required.

## Explicit non-impact

No files, databases, settings, processes, registry keys, startup entries, or
installed artifacts under `C:\Users\tequi\Cloud Scheduler` are read or changed.
No live Outlook or AI call is made.

## External public-reference observation

- Requirements and source context: the design-only observation may inform
  future interpretation of ARC-008, ARC-017, ING-001~002, ING-029, ING-031,
  ING-034, and ING-046~047. It does not add or amend a Requirement, change a
  status, or satisfy a Gate. Its only external source is the public Scribd
  preview recorded in
  `docs/references/2026-08-15-public-oqc-report-observations.md`.
- Files: this reference note and this Impact Map section are the complete
  change. No code, Requirement tracker, test, Fixture, migration, configuration,
  export, or release artifact is changed.
- Normal path: public preview -> short structural paraphrase -> possible future
  Mapping and synthetic-fixture design -> validation against an actual approved
  OQC XLSX before implementation or acceptance.
- Failure path: if the page becomes unavailable, OCR is wrong, or the preview
  conflicts with an actual workbook or approved Spec, the observation is
  ignored. It is not a runtime dependency, fallback data source, threshold,
  supplier disposition, Golden comparison, or acceptance evidence.
- Data and consumers: there is no DB, API, Worker, or UI change yet. Possible
  later consumers are generic template fields, variable sample collections,
  typed numeric and qualitative rows, a separate supplier-result field, exact
  XLSX source locations, and multiple report sections.
- Integration and execution: Scheduler and Outlook contracts are unaffected;
  no Scheduler repository or process is read or changed. The one-local-process
  boundary and all current normal, duplicate, restart, offline, and failure
  behavior remain unchanged.

## User-provided synthetic demo workbook

- Input and provenance: `MASS_PRODUCTION_QUALITY_VALIDATION_OQC_Demo.xlsx` was added by the user. Its own
  `Reference` sheet classifies it as a synthetic planning/demo workbook rather
  than an original supplier report. Its read-only evidence is recorded in
  `docs/references/2026-08-15-mass-production-quality-validation-oqc-demo-workbook-evidence.md`.
- Requirements touched: the file supplies Framework evidence for `ARC-017`,
  `ING-001`, `ING-009~011`, `ING-018`, `ING-029`, `ING-031~032`,
  `ING-034~035`, `ING-037~038`, and `ING-046~047`. None becomes `VERIFIED`
  from this synthetic workbook.
- Code and tests: OPC content-type resolution changed in the Original File
  Store and Workbook Scanner. Mapping domain/application contracts and their
  synthetic integration tests consume Scanner evidence only; the user file is
  not copied into a generated Fixture or embedded in source code. Mapping
  fingerprints now distinguish Worksheet ranges from ChartSheets, whose used
  range must remain absent.
- Normal path: immutable source -> project File Store -> read-only Scanner ->
  exact approved Template -> evidence-only Mapping Preview.
- Failure paths: wrong/ambiguous package type, changed source hash, wrong
  project or supplier evidence, invalid source inspection date, unapproved or
  ineffective Template, fingerprint mismatch, and missing mapped evidence all
  fail closed without an official value or system judgment.
- Consumers and compatibility: this slice adds no HTTP route, UI, Worker,
  business table, or external adapter. Scheduler/Outlook remain untouched.
  In-memory Mapping revision history is Framework-only and does not replace
  later persistent approval/Audit work.

## Persistent Mapping approval and canonical intake slices

- Requirements and sources: `GOV-005`, `GOV-007~008`, `ING-008~012`,
  `ING-018~019`, `ING-046~047`, `ING-049`, `ING-053`, `ARC-008`, `ARC-025~026`,
  and `ARC-030`; Master Implementation Spec sections 3, 4, 7, and 9 plus
  `04_WORK_OQC_INGESTION_MAPPING.md` sections 4, 17, and 19.
- Files and data: add Mapping revision/history/supersession persistence,
  application commands, one Alembic migration, and a canonical application
  orchestrator that composes the existing immutable File Store, Scanner, and
  Mapping Preview. The existing Audit log participates in the same approval
  transaction. No Long-format measurement table is added in these slices.
- Direct consumers: Mapping Preview reads an approved effective catalog;
  manual intake returns one envelope binding the receipt, scan, and preview or
  explicit failure state. Future API/UI/Worker consumers are not introduced.
- Normal path: create immutable Draft revision -> Reviewer review -> Admin
  approval or supersession -> persistent catalog selection -> preserve source
  -> scan stored bytes -> Mapping Preview with identical hash/project/supplier
  provenance.
- Failure and rollback: unauthorized or invalid transitions, stale optimistic
  versions, duplicate revisions, and overlapping approval periods write no
  partial Mapping or Audit state. Scan and Mapping failures retain the raw
  receipt and remain distinct. Unexpected implementation errors are not
  converted into business hold states.
- Restart, duplicate, and concurrency: approved revisions and supersession
  boundaries survive database restart. Same workbook bytes still create a new
  receipt over the same project-local blob. Database optimistic locking rejects
  stale Mapping decisions without automatic retry; File Store concurrency
  remains deliberately single-process.
- Security and scope: commands accept the trusted Actor object and enforce
  Reviewer/Admin boundaries. No internal filesystem path or secret is exposed.
  There is no API, UI, AI, Scheduler/Outlook, external I/O, or Cloud Scheduler
  workspace change. Workbook-driven project inference and Golden acceptance
  remain outside these bounded slices.

## Pending Long-format candidate slice

- Requirements and sources: direct progress is limited to `ARC-008`,
  `ARC-015`, `ARC-026`, `GOV-005`, `ING-015`, `ING-020~022`, `ING-024~025`,
  `ING-027~029`, `ING-034~035`, `ING-044~045`, `ING-047`, and `CFG-004`;
  `CFG-001~002` receive only a versioned canonical-binding framework. Master
  IDs, approved Spec/unit configuration, and actual Golden acceptance remain
  unavailable, so none of these requirements is completed by this slice.
- Files and data: add pure Domain/Application contracts that transform only a
  provenance-valid `PREVIEW_READY` intake outcome into deterministic
  `PENDING` load candidates. Add synthetic contract and integration tests plus
  the Living test-ID/status evidence. This first sub-slice has no database
  table, migration, HTTP route, UI, Worker, export, or external adapter.
- Direct consumers and ownership: the Application builder consumes the
  existing receipt-bound Mapping Preview and an injected, read-only,
  versioned canonical row-binding catalog. It neither infers canonical IDs nor
  mutates the File Store, Mapping catalog, source workbook, or Audit history.
- Normal path: exact receipt/scan/preview provenance -> exact approved row
  binding -> lossless numeric or qualitative sample evidence ->
  `LOADABLE_PENDING`. Supplier claims stay separate and system judgment stays
  `NOT_EVALUATED`; official values, conversion, aggregation, and calculation
  flags remain false.
- Failure and partial paths: a missing or ambiguous row binding holds that row
  without discarding its source evidence. Source/project/supplier/model/LOT
  conflicts hold the whole candidate. Formula/cache/refresh-dependent samples
  are never promoted to official measurements. Other valid rows survive as a
  `PARTIAL_HOLD`; unexpected implementation errors are not converted into a
  business hold.
- Duplicate, restart, and transaction boundary: the logical candidate key
  contains receipt, immutable source hash, Mapping revision, and canonical
  binding revision. Same bytes delivered again retain a distinct receipt.
  This pure sub-slice performs no write and claims no durable replay or
  multi-process guarantee; later Long persistence must use an independent
  transaction so raw receipts are never rolled back.
- Compatibility and excluded scope: no unit inference, standardized value,
  Spec/PASS/FAIL decision, `VALID` state, multi-LOT split, raw-statistic
  recalculation, Scheduler/Outlook/AI call, or workbook-driven project routing
  is introduced. `GOV-013` and `ING-051` remain `BLOCKED_BY_INPUT` pending an
  actual approved representative supplier workbook and 100% comparison.

## Pending Long-format persistence slice

- Requirements and sources: the persistence follow-up continues the same
  partial Phase 1 requirements and additionally touches `ARC-007`, `ARC-030`,
  `GOV-007`, `ING-002`, `ING-039~043`, `CFG-005`, and `CFG-017` only at their
  durable provenance, transaction, and idempotency boundaries. It does not
  complete Queue, Master/Spec, duplicate-decision, correction, or approval
  workflows.
- Files, schema, and migration: add Alembic revision `0003`, Long persistence
  models/repository/application transaction code, and isolated integration and
  migration tests. The bounded schema covers receipt-level source metadata,
  scanned sheet snapshots, ingestion jobs, one pending OQC lot candidate,
  inspection-result candidates, and exact source-cell measurement evidence.
  Every relationship among these Long persistence tables carries project scope
  and uses restrictive foreign keys. The existing Mapping revision is verified
  by project/supplier/revision lookup and exact application re-preview.
- Normal path: independently preserved File Store receipt -> verified Scan and
  approved Mapping provenance -> deterministic pending candidate -> short
  source/job claim transaction -> atomic Long candidate materialization. The
  database stores only `PENDING`/held framework states; it has no path that can
  create `VALID`, a standardized value, or an official system judgment without
  future approved Master/Spec configuration.
- Failure and rollback: source metadata and the job claim survive a fatal Long
  materialization error, while lot/result/measurement rows roll back together
  and the job is marked failed in a separate transaction. Expected row holds
  are committed as explicit evidence, not swallowed exceptions. Raw/File Store
  state never participates in the Long transaction.
- Retry, duplicate, and restart: exact receipt+Mapping+binding+loader replay is
  database-idempotent. A second receipt for identical project-local bytes keeps
  its own source/job history and points to the first materialization instead of
  duplicating Long rows. Scanner contract version is part of both exact replay
  and materialization ownership, so a parsing-contract change never reuses an
  older result. Cross-project reuse is forbidden. Row-version and uniqueness
  checks reject stale or competing claims; unbounded automatic retry is not
  introduced.
- Compatibility and consumers: migration tests must cover fresh install,
  `0002 -> 0003`, downgrade/re-upgrade, and preservation of prior Audit and
  Mapping rows using temporary databases only. No test or migration command may
  use the workspace default database. No API/UI/Worker, Scheduler/Outlook, AI,
  analytics/cache, shipment, production subgroup, or export consumer is added.
- Implemented stable contracts use the separate `DQ-P1-LDB-001~008` namespace
  so the pure `DQ-P1-LONG-001~006` candidate contracts remain independently
  protected. The completed verification used only explicit temporary database
  URLs and did not migrate the workspace default database.

## Offline assumed-Qwen Mapping candidate and Korean OQC sample slice

- User decisions: the eventual cloud model is `Qwen3.5-33B` behind the already
  documented OpenAI-compatible profile, but it cannot be called from this
  environment. This slice must therefore validate the integration and safety
  contract with deterministic offline responses, never claim measured Qwen
  accuracy, and keep actual live-model acceptance blocked.
- Requirements and sources: direct partial progress is limited to `GOV-004`,
  `GOV-005`, `GOV-008`, `ARC-014~016`, `ING-008~010`, `ING-018~019`,
  `ING-024~025`, `CFG-004`, `CFG-017`, and the AI contract in Master sections
  3, 20, and 21 plus
  `04_WORK_OQC_INGESTION_MAPPING.md`. Existing `EXC-001~008`, `ING-034~035`,
  and `CFG-017` remain negative boundaries rather than AI completion claims.
- Workbook outputs: create five explicitly synthetic Korean OQC workbooks under
  one `outputs/` thread directory: baseline, same-format historical, structural
  change, ambiguous structure, and business/formula-error cases. Each workbook
  declares that it is fictional Framework evidence, contains no real supplier
  or person, and has a machine-readable scenario sheet. The user Demo and
  immutable baseline ZIP remain read-only and are not copied into the outputs.
- Code and ownership: add a provider-neutral Domain/Application contract that
  builds a bounded structural request from `WorkbookScan`, consumes one strict
  JSON response, verifies every proposed Sheet/Cell against preserved Scanner
  evidence, and returns review-only candidates. Add a reproducible Korean OQC
  sample builder and synthetic Scanner-to-AI contract tests. No live provider
  adapter, HTTP route, DB table, UI, Worker, or Scheduler code is added.
- Normal path: Scanner evidence -> minimized untrusted structural payload ->
  schema-valid AI response -> exact existing Cell verification ->
  `REVIEW_REQUIRED` Mapping candidates. AI candidates never create or approve a
  Mapping Template and never create official values, calculations, Spec, unit
  conversion, PASS/FAIL, or system judgment.
- Failure and safety paths: disabled provider, timeout/failure, malformed JSON,
  unknown fields, missing/duplicate/nonexistent coordinates, wrong source
  tokens, excessive output, prompt-injection cell text, and attempted official
  actions all fail closed. Scanner/File Store/manual Mapping continue to work;
  AI failure never changes the canonical route state.
- Privacy and determinism: no API key, endpoint, credential reference, mail
  body, file path, attachment bytes, image bytes, or full workbook is included.
  Header/source tokens are length-bounded and tagged as untrusted data. The
  offline provider is deterministic and has no network or secret access.
- Stable contracts, one marker each:
  `DQ-P1-AIMAP-001` minimized bounded text/scalar structure payload,
  `DQ-P1-AIMAP-002` strict exact-cell response,
  `DQ-P1-AIMAP-003` malformed/hallucinated/duplicate/source rejection,
  `DQ-P1-AIMAP-004` disabled/timeout/provider-failure Core independence,
  `DQ-P1-AIMAP-005` forbidden official action rejection,
  `DQ-P1-AIMAP-006` review-only/no official effect,
  `DQ-P1-AIMAP-007` five Korean workbook Scanner-to-AI scenarios,
  `DQ-P1-AIMAP-008` prompt-injection provider zero-call,
  `DQ-P1-AIMAP-009` partial/zero-candidate unresolved and warning preservation,
  `DQ-P1-AIMAP-010` approved Mapping provider zero-call,
  `DQ-P1-AIMAP-011` request-digest equality, and
  `DQ-P1-AIMAP-012` runtime-unverified Qwen assumption with no endpoint/key.
- Evidence limit: passing this slice proves deterministic framework behavior
  under assumed Qwen-shaped responses only. It does not verify actual
  `Qwen3.5-33B` accuracy, latency, token limits, endpoint compatibility, or live
  secret resolution. `GOV-013` and `ING-051` remain `BLOCKED_BY_INPUT`.

## Korean synthetic OQC full Data Engine acceptance slice

- User decision: stop asking for each unambiguous synthetic Cell and continue
  automatically through the next stage whenever the current Gate passes. Only
  real supplier/Master/threshold decisions that cannot be inferred safely stay
  blocked.
- Requirements and sources: bounded progress covers `ARC-008`, `ARC-015`,
  `ARC-026`, `ARC-030`, `ING-001`, `ING-002`, `ING-008~012`, `ING-018~020`,
  `ING-024~025`, `ING-029`, `ING-034~035`, `ING-045~047`, `CFG-001~002`,
  `CFG-004`, and `CFG-017`, under Master Data Engine and Ingestion/Mapping
  contracts. No synthetic result may close `GOV-013` or `ING-051`.
- Files and data: use the five generated Korean OQC files through their
  reproducible builder in temporary test stores. Final output workbooks and the
  user Demo remain read-only. Add only reusable test-support mappings and
  end-to-end acceptance tests unless a real production defect requires a
  narrowly scoped code fix.
- Normal path: preserve baseline -> scan all sheets -> persist Draft/Review/
  Admin-approved full row-oriented Template -> exact Preview -> exact approved
  canonical row bindings -> pending Long candidate -> temporary SQLite Long
  persistence. Verify every identifier, every inspection row, every sample
  Cell, Mapping revision, source hash, and supplier-vs-system judgment boundary.
- Reuse path: the same approved baseline Template must apply to the same-format
  historical workbook with changed volatile values and create a distinct
  receipt/pending job while retaining deterministic structure and provenance.
- Failure paths: changed layout must not reuse the old Template; ambiguous
  multi-report structure must remain Mapping-required; broken/external formula,
  missing identifier, protection/hidden metadata, and prompt-like text must
  remain explicit and must never be converted into official values or an AI
  instruction. Raw receipts survive every hold.
- Persistence and rollback: use temporary databases and temporary File Stores
  only. No workspace DB migration or default database access. Pending/held is
  the maximum state; `VALID`, standardized value, official PASS/FAIL, Spec
  approval, analytics, cache, API/UI/Worker, and Scheduler remain absent.
- Stable acceptance contracts will use `DQ-P1-KOQC-001~006`: complete baseline
  mapping, same-format reuse, changed-layout hold, ambiguity/error hold, full
  pending Long candidate, and temporary DB persistence/replay.
- Success transition: after this slice is green, start the next separate Impact
  Map for persistent audited canonical model/part/item and Master Spec/unit
  configuration. Real business values remain versioned input, never hidden
  defaults.

## Mapping schema v2 complete source-evidence roles

- Trigger and scope: the Korean synthetic acceptance Gate passed, but its
  Scanner retained source cells that Mapping schema v1 cannot name. Extend the
  versioned Mapping/Preview/Long evidence contract before any Master or
  `VALID` promotion work. This is source preservation only, not a business
  interpretation or official calculation.
- Requirements and sources: direct bounded progress for `GOV-005`, `ING-020`,
  `ING-025`, `ING-028`, `ING-029`, `ING-034`, `ING-047`, `CFG-004`, and
  `CFG-011`, using Master Spec and Ingestion/Mapping sections for identifiers,
  row context, Spec evidence, and exact Source Cell traceability. `ING-004~006`,
  `ING-023~027`, official Master configuration, and Golden acceptance remain
  incomplete.
- Domain changes: schema v2 may map `PART_NAME`, `PRODUCTION_DATE`,
  `CURRENT_SHIPMENT_QUANTITY`, and
  `SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY` identifiers. Inspection rows may
  optionally map section, category, unit, measurement point, measurement
  location, cavity, Target, LSL, USL, and source Spec revision in addition to
  existing evidence. Every mapped value retains the exact Sheet/Cell, stored
  value, display status, formula/cache, number format, and value kind.
- Compatibility: schema v1 remains supported with an unchanged serialized
  payload and rejects v2-only roles. Schema v2 serialization is deterministic,
  persistent, digest-protected, and reloadable. Missing optional roles remain
  explicit `None`; they are never inferred from headers, neighboring cells, or
  AI output.
- Preview and Long boundary: v2 fields flow through Mapping Preview, pending
  Long candidate snapshots, identifier JSON, and row source-evidence JSON.
  They do not set canonical IDs, standardize units, split production groups,
  evaluate Spec, calculate shipment totals, or create a system judgment. No DB
  migration is expected because the current persistence boundary stores these
  payloads as versioned JSON; migration metadata diff must remain empty.
- Failure paths: unsupported schema, a v1 Template carrying v2 roles, duplicate
  semantic roles, nonexistent/blank mapped cells, payload tampering, and
  source-scope conflicts fail closed. Same item text with different method,
  unit, location, or cavity remains separate evidence and is not auto-merged.
- Stable contracts, one marker each:
  `DQ-P1-MAPV2-001` schema-v1 payload compatibility,
  `DQ-P1-MAPV2-002` extended identifier evidence,
  `DQ-P1-MAPV2-003` extended row/Spec evidence,
  `DQ-P1-MAPV2-004` persistent v2 round-trip/digest,
  `DQ-P1-MAPV2-005` Preview-to-pending-Long evidence propagation,
  `DQ-P1-MAPV2-006` optional/no-inference and semantic separation, and
  `DQ-P1-MAPV2-007` no conversion/calculation/judgment plus persistence replay.
- Files and consumers: bounded changes are expected in Mapping and Long Domain,
  Mapping Preview/Long candidate Application services, Mapping/Long JSON
  infrastructure, and focused tests/traceability only. No API, UI, Worker,
  Scheduler, live AI, default database, original workbook, or output workbook
  change is authorized by this slice.

## Scanner broken-reference text false-positive hardening

- Trigger: independent Korean OQC review found that a normal text cell merely
  mentioning `#REF!` can be classified as a broken formula. This can create a
  false `CALCULATION_REFRESH_REQUIRED` warning even though the source is plain
  explanatory text.
- Requirement and source: `ING-035` under the Excel safety contract. Actual
  broken formulas/error values must remain explicit, while ordinary text must
  not be promoted into formula evidence.
- Change boundary: narrow `OpenpyxlWorkbookScanner` Cell issue classification
  and its synthetic Scanner tests only. Treat `#REF!` as broken-reference
  evidence when it is an actual Excel error value, occurs in formula text, or
  is the cached result of a formula. Plain string cells remain exact text and
  receive no broken-reference/refresh issue solely for mentioning the token.
- Regression: `DQ-P1-SCAN-006` covers plain-text non-warning plus actual
  formula/error warning and exact Source Cell location. Existing `SCAN-003`
  external/broken/cache contracts must remain green.
- No Mapping, DB, API, UI, Worker, Scheduler, AI, original workbook, output
  workbook, or default database change is authorized by this hardening slice.

## Persistent canonical hierarchy, row binding, and Master Spec framework

- Trigger: schema-v2 source evidence and pending Long persistence now pass, but
  canonical model/part/item bindings are caller-created snapshots and there is
  no approved Master Spec store. A persistent, audited approval boundary must
  exist before any row can move beyond `PENDING` or any source Spec can be
  compared officially.
- Requirements and sources: bounded framework progress for `GOV-007`,
  `GOV-008`, `ARC-008`, `CFG-001~007`, `CFG-009`, `CFG-016~017`, and
  `ING-023~025`, based on the Master Configuration and Ingestion/Mapping
  contracts. Actual Master values, qualitative acceptance rules, method
  appropriateness, submission standards, promotion to `VALID`, and Golden
  acceptance remain separate work.
- Canonical hierarchy: persist project-isolated model, model-part, supplier
  axis, and inspection-item identities. Supplier remains a comparison/binding
  axis rather than a parent in the model -> part -> item hierarchy. Items have
  an explicit `CANDIDATE`, `MANAGED`, or `EXCLUDED` disposition; no candidate
  enters official analysis automatically.
- Master Spec revision: persist immutable numeric-limit revisions with Target,
  LSL, USL, unit, external Spec revision label, declared effectivity, change
  reason, source reference, review/approval metadata, and resolved
  supersession. Source/OQC Spec evidence is never copied into this table by an
  ingestion path. Missing actual values remain input-blocked rather than
  provisional defaults.
- Canonical row binding: persist exact project/supplier/template-revision/
  row-key binding revisions to canonical model/part/item keys, exact source
  model aliases, sample policy, measurement mode, effectivity, and workflow
  decisions. Only approved/effective revisions materialize into the existing
  `CanonicalRowBindingCatalog`; Draft/Reviewed/expired/future rows are excluded.
- Workflow and concurrency: trusted pre-auth Actor commands use Reviewer for
  review and Admin for final approval/supersession. One local owner may hold
  both roles, but every decision is separate and audited. Immutable payloads,
  row-version optimistic locking, no revision overwrite/downgrade, non-overlap
  of approved periods, cross-project/scope FK isolation, and same-transaction
  Audit rollback are required.
- Persistence: add Alembic `0004` with only the new canonical/Master tables and
  indexes. Fresh install, `0003 -> 0004`, downgrade/re-upgrade, prior Audit/
  Mapping/Long row preservation, metadata parity, and explicit-URL isolation
  use temporary databases only. No default workspace DB is touched during
  tests or review.
- Negative boundary: this slice creates no Master from supplier text, no unit
  conversion, Spec evaluation, PASS/FAIL, `VALID` row, analytics, cache, API,
  UI, Worker, Scheduler, live AI, email, or external write. Loading an approved
  catalog is read-only and snapshot-based; later approvals require reload.
- Stable contracts, one marker each:
  `DQ-P1-MASTER-001` project-isolated hierarchy/supplier axis,
  `MASTER-002` item disposition and optimistic Audit,
  `MASTER-003` Master Spec Draft/Review/Admin approval,
  `MASTER-004` immutable revision/effectivity/supersession,
  `MASTER-005` supplier/cross-scope/role fail-closed boundary,
  `MASTER-006` persistent canonical row-binding workflow/catalog,
  `MASTER-007` approved/effective-only deterministic selection,
  `MASTER-008` `0004` migration and prior-row preservation,
  `MASTER-009` atomic rollback/concurrency/audit evidence, and
  `MASTER-010` no automatic promotion/calculation/official judgment.
- Expected files: new Master Domain/Application/Infrastructure modules,
  Alembic `0004`, migration metadata import, focused tests, and living
  traceability. Existing Mapping/Long behavior is preserved. Original ZIP,
  workbooks, outputs, Scheduler workspace, default DB, and secrets remain
  untouched.

### Completion evidence

- Result: bounded framework slice `PASS`; all related baseline requirements
  remain `IN_PROGRESS`, and Phase 1 remains open.
- Code: `domain/master_config.py`, `application/master_config_commands.py`,
  `infrastructure/master_config.py`, Mapping composite-scope indexes, and
  Alembic `0004` implement the planned project, workflow, CAS, Audit, and
  declared/resolved effectivity boundaries.
- Tests: `DQ-P1-MASTER-001~010` each exist once. The current Windows Gate passes
  105 tests, 95 stable IDs, no skip/xfail, Ruff/format 68 files, strict Mypy 45
  sources, requirement integrity `333 + 13 / 81 / 69`, and migration graph
  `0001 -> 0004`.
- Migration evidence: fresh, `0003 -> 0004`, downgrade, and re-upgrade use only
  explicit temporary SQLite URLs; Alembic metadata diff is empty and prior
  Audit/Mapping/source/job/lot/result/measurement identity, status, raw, SHA,
  and version snapshots remain exact.
- Negative evidence: supplier/OQC values cannot populate Master, candidates
  cannot be approved, an excluded item may retain source linkage only, and no
  unit conversion, Spec evaluation, official judgment, `VALID` transition,
  API/UI/Worker/Scheduler/live-AI/default-DB write exists.
- Resume point: build a separate approved-Master evaluation/review candidate
  and explicit audited data-status decision slice. Do not auto-promote pending
  data and do not fabricate missing production Master values.

## Approved-Master review candidate and explicit data-status decision

- Trigger: approved numeric Master revisions and pending Long rows now persist,
  but there is no deterministic comparison candidate or authorized transition
  from evidence-only `PENDING` into the official data-trust state machine.
- Requirements and sources: bounded progress for `GOV-004`, `GOV-005`,
  `GOV-007`, `GOV-008`, `ARC-008`, `ARC-015`, `ARC-026`, `ING-028`,
  `ING-041`, `ING-045`, `CFG-004~007`, `CFG-009`, `CFG-016~017`, and the
  `VALID`-only selector foundation of `ANA-011`. `ING-027` conversion,
  `ING-042` replacement chains, Trend/Control Limits, qualitative Master,
  actual production values, and Golden acceptance remain separate work.
- Semantic boundary: data trust and Spec judgment are orthogonal. A trustworthy
  out-of-limit measurement is `FAIL + VALID`; no code may turn a FAIL into
  SUSPECT/EXCLUDED automatically. Candidate generation is read-only and never
  changes a state. Only a trusted pre-auth local Admin command may decide
  `PENDING -> VALID | SUSPECT | EXCLUDED`.
- Aggregate boundary: `inspection_results` is the concurrency root. Every
  measurement belonging to one result receives the same status in one
  transaction. `oqc_lots.data_status` remains the ingestion aggregate and is
  not fabricated into a final trust state. One held or review-only result does
  not block another result in the same Lot.
- Eligibility: `HELD` is structural/Mapping hold and cannot transition here.
  `CANDIDATE` items require a disposition decision first. `EXCLUDED` items may
  only be explicitly excluded. A managed numeric item becomes `EVALUATED` only
  when the inspection-date catalog returns exactly one approved/effective
  Master, all stored evidence/digests and exact bindings are intact, every
  sample is evaluable, and the source unit exactly equals the Master unit.
- Numeric contract: compare exact stored numeric evidence against inclusive
  one- or two-sided Decimal limits; Target is not a PASS/FAIL boundary. Missing
  or unequal units, aliases, scale changes, non-numeric or non-finite values,
  formulas requiring refresh, and qualitative rows remain review-only or
  ineligible. No unit inference, conversion, standardized value, Cpk, Trend,
  Outlier, supplier score, or AI call is authorized.
- Provenance: the immutable review candidate binds result and ordered
  measurement IDs/versions/evidence hashes/raw tagged values; source and
  binding hashes; inspection date; item identity/disposition/version; exact
  Master history/revision IDs, versions, payload hash, declared/resolved period;
  per-sample comparison; and a versioned deterministic digest. Volatile clocks
  are excluded from the digest.
- Command and idempotency: the service rebuilds the candidate inside the write
  transaction, verifies expected result/measurement/Master versions and digest,
  then updates the result and every measurement, inserts one immutable status
  transition, and appends generic Audit atomically. A command ID is idempotent
  for the same payload and rejected if reused with different intent. Stale or
  concurrent work, row-count drift, digest changes, or Audit failure rolls all
  mutations back.
- Persistence: add Alembic `0005`. Expand result/measurement status checks for
  the complete trust enum while keeping Long materialization restricted to
  `PENDING`/`HELD`; add exact applied-Master/current decision projection to the
  result and an append-only project-scoped transition table with composite
  Master/result/source foreign keys. Official selectors require both result and
  measurement `VALID`. Existing rows and payloads remain unchanged on upgrade.
  A downgrade that would discard terminal decisions must fail closed rather
  than silently lose history.
- Migration/test isolation: fresh install, `0004 -> 0005`, safe downgrade and
  re-upgrade, Alembic metadata parity, and preservation of prior Audit/Mapping/
  Long/Master rows use explicit temporary SQLite URLs only. No default DB,
  original workbook, output workbook, Scheduler, secret, live AI, email, API,
  UI, Worker, analytics, or external write is in scope.
- Stable contracts, one marker each:
  `DQ-P1-DSTAT-001` exact Master selection and deterministic evaluation,
  `DSTAT-002` zero automatic promotion/calculation/AI/conversion,
  `DSTAT-003` explicit Admin transition with FAIL+VALID and sample propagation,
  `DSTAT-004` role rejection with zero mutation,
  `DSTAT-005` unit fail-closed behavior,
  `DSTAT-006` held/candidate/Master/integrity failures plus item isolation,
  `DSTAT-007` historical Master provenance and immutable prior decisions,
  `DSTAT-008` stale/concurrent/idempotent restart behavior,
  `DSTAT-009` atomic Audit rollback and decision evidence, and
  `DSTAT-010` `0005` migration preservation plus VALID-only selector.
- Expected files and consumers: new Data Review Domain/Application/
  Infrastructure modules, minimal Master record selector, Alembic `0005`,
  migration metadata import, focused tests, and living traceability. Existing
  File Store, Scanner, Mapping, pending Long materializer, Master commands, and
  all failure/replay paths must remain green without changing their public
  behavior.

### Completion evidence

- Result: bounded data-review slice `PASS`; Phase 1 remains `IN_PROGRESS`.
- Code: `domain/data_review.py`, `application/data_review.py`,
  `infrastructure/data_review.py`, the exact persisted-Master selector, Long
  trust/projection constraints, and Alembic `0005` implement the planned
  read-only candidate and explicit Admin decision boundary.
- Tests: `DQ-P1-DSTAT-001~010` each exist once. The current Windows release Gate passes
  122 tests, 112 stable IDs, no skip/xfail, Ruff/format 78 files, strict Mypy
  52 sources, requirement integrity `333 + 13 / 83 / 71`, and migration graph
  `0001 -> 0005`.
- Safety evidence: no automatic `VALID`; `FAIL + VALID` remains possible; HELD
  cannot transition; one result and all measurements change atomically while
  the Lot is unchanged; source/Long/Master/Audit/replay tampering fails closed.
- Migration evidence: corrupt `0004 -> 0005` upgrade rolls back with artifact
  count zero and clean FKs; safe downgrade/re-upgrade preserves data; terminal
  decisions refuse downgrade before mutation. All URLs and working directories
  are temporary, with no default DB access.
- Deferred/blocked: `REPLACED` transition chains and later recovery are
  `DEFERRED_BY_PHASE`; actual company Master/qualitative rules and Golden OQC
  acceptance remain `BLOCKED_BY_INPUT`.
- Resume point at this checkpoint: build the first Korean local UI/API surface;
  that work is completed in the following slice without adding calculations.

## Korean local manual-intake UI and asynchronous API

- Trigger: the Data Engine contracts are green, but a user still cannot start
  Mass Production Quality Validation in a browser, choose an OQC workbook, or see preservation/scan and
  Mapping-hold evidence. This slice creates the first visible personal-local
  workflow instead of another internal rule framework.
- Requirements and sources: bounded progress for `GOV-005`, `GOV-011`,
  `ING-007`, `ING-009`, `ING-010`, `ING-038`, `ING-046`, `ING-047`,
  `ING-049`, `ING-052`, `ARC-026`, `ARC-029`, and `ARC-030`, based on the
  Ingestion/Mapping and Master Implementation contracts. Dashboard `UIX-*`,
  persistent user authentication, Scheduler intake, live Qwen, automatic
  Mapping approval, and Golden acceptance remain outside this slice.
- User path: a Korean page accepts one `.xlsx`/`.xlsm`, an explicit project
  key, and optional model/LOT hints. Upload validation and bounded staging run
  first; workbook parsing runs off the HTTP request thread. The page polls a
  job and renders queued/running, raw-preserved Mapping-required, known scan
  failure, unexpected failure, and capacity-rejected states without exposing
  an internal path.
- Application boundary: reuse `OriginalFileStore`,
  `ManualWorkbookIngestionService`, and `OpenpyxlWorkbookScanner`. Successful
  scan remains `MAPPING_REQUIRED` until an approved Mapping is explicitly
  selected or created; this UI never fabricates a Template. A scan failure
  still exposes the preserved Receipt and exact safe issue code.
- Runtime boundary: one local process owns a bounded background queue and a
  session job registry. Upload bytes are streamed to a project-neutral staging
  file with an explicit limit; the worker preserves them into the
  project-isolated File Store before scanning. Staging is removed on every
  terminal path. Restart loses only the transient screen job list, never the
  already preserved raw file. Durable UI-job recovery is a later Worker slice.
- API boundary: versioned `/api/v1/intake/jobs` create/read contracts use
  opaque job IDs, require the same normalized project key for reads, redact
  paths and exception text, and return Korean display labels plus stable
  machine codes. No delete, file mutation, Mail/Outlook, Scheduler, AI, DB
  Master change, or external write route is added.
- Frontend boundary: React/TypeScript/Vite produces a Korean, keyboard-usable,
  responsive shell with file picker/drop target, project field, progress,
  receipt/hash summary, Sheet visibility/range table, and actionable warnings.
  It shows that a result is preserved but not yet officially registered. It
  must not display PASS/FAIL, VALID, Cpk, supplier score, or invented values.
- Verification: Python API/application tests cover normal, invalid,
  oversize/MIME, raw-preserved scan failure, project isolation, bounded queue,
  shutdown, redaction, and no-Core-blocking paths. Frontend component tests
  cover Korean labels, selection/submission/polling, terminal states, error
  recovery, and keyboard/accessibility behavior. Production build, backend
  static serving, Windows localhost browser smoke, and source-hash preservation
  are release evidence.
- Stable IDs, one marker each: `DQ-P1-UIINTAKE-001` API upload and
  asynchronous successful scan/Mapping hold; `UIINTAKE-002` validation and no
  residue/capacity; `UIINTAKE-003` project isolation and opaque read;
  `UIINTAKE-004` raw-preserved scan failure and redaction; `UIINTAKE-005` Korean
  static application serving; `UIINTAKE-006` application lifecycle/restart;
  `UIINTAKE-007` actual workbook/hash/browser smoke boundary.
- Compatibility: no new DB table or migration is planned for this first
  screen. Alembic remains `0005`. Existing Engine APIs, original ZIP/workbooks,
  default database, Scheduler workspace, secrets, and outputs remain unchanged.

### Completion evidence

- Result: Korean local manual-intake UI/API slice `PASS`; Phase 1 remains
  `IN_PROGRESS`.
- Code: the bounded `IntakeJobManager`, safe FastAPI intake router, FastAPI
  lifecycle/static serving, and React/TypeScript Korean UI compose the existing
  File Store and Scanner without new business rules or DB schema.
- Automated evidence: `DQ-P1-UIINTAKE-001~007` each exist once; backend full
  Gate is 122/122, frontend Vitest is 13/13, production Vite build passes,
  Ruff/format covers 78 files, strict Mypy covers 52 sources, and skip/xfail is
  zero.
- Browser evidence:
  `reports/acceptance/2026-08-15-local-intake-browser-smoke.md`. A real Chrome
  session selected the Korean synthetic baseline, reached `MAPPING_REQUIRED`,
  displayed the unchanged `782283EE...FEBEBF` hash, three Sheet structures and
  exact safe formula-cache warnings. Desktop and 390px layouts had no
  horizontal overflow and console warning/error was zero.
- Runtime evidence: upload staging was empty at terminal, the isolated File
  Store contained exactly one Blob plus one Receipt, and the local server,
  browser tab, viewport override, and port were closed after verification.
- Safety boundary: the first screen stops at Mapping hold. Its job list is
  intentionally process-local; preserved raw evidence is durable. It does not
  approve Mapping, write official Long rows, decide data status, call Qwen, or
  access Scheduler/Outlook.
- Resume point: connect the preserved Receipt/Scan to the persistent approved
  Mapping catalog, expose exact Mapping Preview and review/approval commands in
  Korean, and retain fail-closed behavior when no approved Template exists.

## Windows personal local extension package framework

- Trigger: the Korean localhost application is usable from the Repository, but
  an opt-in user still lacks a reproducible code package, isolated install/data
  layout, safe update/remove transaction, and one-instance Windows launcher.
- Requirements and sources: bounded framework progress for `ARC-022`,
  `ARC-023`, and `ARC-028`, based on the Living deployment decisions in System
  Architecture and Roadmap. This does not start Phase 5 Scheduler integration.
- Artifact boundary: a deterministic ZIP contains the versioned extension
  manifest, exact per-file SHA-256 inventory, Backend runtime source and
  migrations, prebuilt Frontend, hashed Python runtime lock, installer tool,
  and localhost launcher. Development dependencies, tests, original files,
  user data, secrets, and build caches are excluded.
- Compatibility boundary: the manifest records Mass Production Quality Validation and contract majors,
  while Scheduler compatibility and discovery remain explicitly `UNVERIFIED`,
  `PHASE_5`, and `BLOCKED_BY_INPUT`. No Scheduler workspace, database, process,
  registry, service, autostart entry, mail, Outlook, or live AI is accessed.
- Install/update/remove boundary: callers provide or deliberately accept
  current-user code/data roots that must be disjoint. Install and update verify
  every packaged byte, prepare a Python 3.12 virtual environment from the
  hash-locked runtime requirements, then atomically swap code with rollback.
  Dry-run performs no write. Remove deletes installed code only; user data is
  preserved by default and this slice exposes no data-delete operation.
- Runtime boundary: the launcher injects absolute SQLite, Original File Store,
  intake staging, and prebuilt Frontend paths below the data/code roots. A
  localhost health check plus a per-install named mutex prevents a second DQ
  NEXUS instance, then the launcher opens the default browser. It creates no
  persistent OS integration.
- Dependency limitation: the package includes a hash-locked dependency recipe,
  not an offline wheelhouse or embedded Python. Default install therefore
  requires Python 3.12 plus package-index or cache availability. A Python-free,
  fully offline installer is later release hardening, not claimed here.
- Stable contracts, one marker each: `DQ-P1-WINPKG-001` deterministic complete
  artifact; `WINPKG-002` manifest/version/compatibility and hash verification;
  `WINPKG-003` dry-run and path safety; `WINPKG-004` atomic install/update
  rollback; `WINPKG-005` code-only remove with data preservation; and
  `WINPKG-006` localhost one-instance launcher and forbidden-integration
  negative boundary.
- Expected files: packaging metadata/launcher, new `scripts/release` builder
  and installer core/wrappers, isolated temporary-path tests, and later living
  traceability integration. Existing application code, database schema,
  Scheduler, original inputs, and default `.localdata` remain unchanged.

## Receipt-bound manual Mapping draft, review, and approval API

- Trigger: manual intake preserves a Receipt and exact Workbook scan, but the
  user cannot yet bind selected source cells to a persistent Mapping revision
  or complete the already-defined reviewed/approved workflow.
- Requirements and sources: bounded progress for `GOV-005`, `GOV-007~008`,
  `ING-009~012`, `ING-015`, `ING-018`, `ING-038`, `ING-046`, `ING-047`,
  `ING-049`, `ARC-026`, and `ARC-029~030`, using
  the existing Mapping schema-v2 Domain, persistent command service, CAS, and
  Audit transaction. New-history revision 1 only is supported in this slice;
  editing or appending a later revision remains explicit future work.
- Trust boundary: HTTP bodies never accept actor IDs or roles. The local app
  injects the trusted `LOCAL_OWNER`; review and approval remain separate
  commands, and the Domain still forbids direct `DRAFT -> APPROVED`.
- Source boundary: every draft/review/approve command supplies project,
  Receipt ID, content SHA-256, and supplier scope. The server reloads the exact
  project-local Receipt, opens the immutable stored Blob, rescans it, and
  rejects any identity, size, name, hash, or scope disagreement.
- Selection boundary: draft input contains exact Sheet/A1 cells for identifier
  and row roles plus at least one user-selected header anchor. `SUPPLIER` and
  `INSPECTION_DATE` mappings are mandatory. The supplier alias is generated
  only from the selected nonblank supplier source cell; no free-form alias or
  hidden default is accepted. `effective_from` is required input and
  `effective_to` is optional.
- Fingerprint boundary: the server—not the browser—derives header tokens, every
  scanned Sheet structure/order/visibility/range, every merge signature, and
  complete non-empty row signatures containing all mapped cells. A prospective
  approved preview must be exactly `PREVIEW_READY` before each workflow write.
- Approval evidence: after ADMIN approval, the service reloads a fresh
  persistent catalog and applies it to the same Receipt scan. The response is
  successful only with `PREVIEW_READY`, exact source date, and
  `NOT_EVALUATED`; no official value, Long row, calculation, AI call, judgment,
  automatic approval, or supplier-to-Master copy is created.
- API boundary: versioned draft/review/approve POST routes return Korean-safe
  errors, immutable workflow row versions, source/fingerprint proof, and
  explicit `additional_revisions_supported=false`. Internal paths and raw
  exceptions remain redacted.
- Stable contracts, one marker each: `DQ-P1-MAPUI-005` exact receipt-bound v2
  Draft and server fingerprint; `MAPUI-006` separate trusted review with CAS
  and Audit; `MAPUI-007` ADMIN approval plus fresh-catalog same-source preview;
  `MAPUI-008` source/scope/cell/role/tamper fail-closed behavior; and
  `MAPUI-009` direct approval, forged actor, stale version, automation, and
  official-output negative boundaries.
- Expected files and consumers: new Mapping registration Application
  orchestration and API router, minimal API export/main factory wiring, focused
  temporary File Store/SQLite tests, and the parallel Korean Frontend client.
  No migration, Scheduler, default DB, original input, output workbook, AI,
  Master, Long persistence, or data-status mutation is planned.

## Receipt-bound pending Long candidate and explicit confirmation API

- Trigger: an approved exact Mapping can now be replayed from one immutable
  Receipt, but the local UI cannot yet inspect the existing canonical-binding
  result or explicitly confirm the already-defined pending Long persistence.
- Requirements and sources: bounded progress for `ARC-007~008`, `ARC-015`,
  `ARC-026`, `ARC-030`, `GOV-005`, `ING-015`, `ING-020~022`,
  `ING-024~025`, `ING-027~029`, `ING-034~035`, `ING-041`,
  `ING-044~045`, `ING-047`, `CFG-004`, and `CFG-017`. This composes the
  existing approved Mapping, persistent canonical row-binding catalog, pure
  Long candidate builder, and pending-only Long persistence; it does not
  extend any of their business rules.
- Read boundary: candidate requests carry exact project, Receipt ID, content
  SHA-256, and supplier scope. The server reopens and rescans the immutable
  source, loads a fresh approved Mapping catalog, reconstructs one
  `PREVIEW_READY` outcome, and loads the approved/effective binding catalog as
  of the source inspection date. Candidate lookup is deterministic and writes
  zero database rows.
- Candidate contract: each row is shown as `LOADABLE_PENDING` or `ROW_HELD`
  with stable typed issues, exact source coordinates, binding provenance, and
  one canonical candidate SHA-256. A missing binding is a normal explicit
  hold; no binding, item, alias, Master, or Spec is inferred or created.
- Confirmation boundary: the body must repeat the exact Receipt scope, supply
  the candidate digest, and set an explicit confirmation flag. Inside the
  command the server reconstructs the complete source/Mapping/binding
  candidate again and rejects stale, tampered, missing, or cross-project
  evidence before calling the existing `LongPersistenceService`.
- Persistence and replay: only pending/held framework states are stored.
  Exact restart/replay reuses the existing ingestion job without duplicate
  Lot/result/measurement rows; same bytes under another Receipt retain the
  existing persistence rules. Loader and scan-contract versions are
  server-owned, not browser inputs.
- API and safety: `POST /api/v1/long/candidates` is read-only and
  `POST /api/v1/long/confirmations` is the sole explicit write. Responses
  expose candidate/job state, row-version/count evidence, and set official
  values, calculations, AI calls, and `VALID` creation to false. Safe errors
  redact filesystem paths and raw exceptions.
- Stable contracts, one marker each: `DQ-P1-LONGUI-001` exact zero-write
  candidate; `LONGUI-002` loadable source/binding provenance; `LONGUI-003`
  missing-binding and partial/global hold; `LONGUI-004` explicit confirmation
  pending/held materialization; `LONGUI-005` stale/tamper/scope fail-closed;
  and `LONGUI-006` restart/idempotent replay with zero duplicate rows.
- Expected files and consumers: new Long workflow Application orchestration
  and HTTP router, minimal API export/main factory wiring, focused temporary
  SQLite/File Store tests, and the parallel Korean Frontend client. No schema
  migration, Scheduler, AI, Master Spec decision, data-status promotion,
  default database, original input, or output workbook change is planned.

## Explicit data-status review candidate and ADMIN decision API

- Trigger: confirmed Long persistence now creates project-local PENDING/HELD
  inspection results, but the local UI cannot yet inspect the existing
  approved-Master review candidate or submit the already-defined explicit
  terminal trust decision.
- Requirements and sources: bounded progress for the existing Phase-1 data
  review framework (`DQ-P1-DSTAT-001~010`) and UI contracts
  `DQ-P1-DSTATUI-001~006`. This slice reuses `DataStatusReviewService`, its
  immutable Long/Master evidence reconstruction, 0005 projection, transition,
  CAS, idempotent command, and same-transaction Audit behavior without adding
  a new calculation or state rule.
- Read boundary: `POST /api/v1/data-reviews/targets` resolves deterministic
  PENDING/HELD result targets from the confirmed Long job without asking the
  browser to invent an internal result ID. `POST /api/v1/data-reviews/candidates`
  accepts only exact project/result identity and rebuilds a deterministic read-only candidate
  from persisted Long evidence and the approved/effective Master at the source
  inspection date. Result status, independent proposed PASS/FAIL evidence,
  exact unit, raw samples, Master revision/effectivity, issues, eligible target
  statuses, and every CAS version are returned without mutation.
- Decision boundary: `POST /api/v1/data-reviews/decisions` requires an explicit
  target (`VALID`, `SUSPECT`, or `EXCLUDED`), nonblank reason, confirmation,
  candidate SHA-256, and the complete candidate CAS receipt. The server derives
  the command identity, injects trusted `LOCAL_OWNER` ADMIN authority, and the
  existing service rebuilds and locks the candidate before one atomic
  PENDING transition. Actor, roles, server versions, idempotency keys, Master
  values, and judgments are never accepted from the browser.
- Trust boundary: `FAIL + VALID` remains allowed when exact approved Master,
  unit, and numeric evidence make the candidate EVALUATED. HELD, structurally
  INELIGIBLE, stale, mismatched, or disallowed REVIEW_ONLY requests fail
  closed. No automatic decision, unit conversion, standardized value, AI,
  supplier-spec adoption, or calculation beyond the existing exact Master
  comparison is introduced.
- Replay and safety: the deterministic server-owned command ID makes an exact
  retry return the existing transition without duplicate transition/Audit or
  measurement updates. Project/result scope, digest, measurement versions,
  item version, and selected Master versions/hash are all revalidated. HTTP
  errors expose stable codes and Korean-safe messages, never database paths or
  raw exceptions.
- Stable contracts, one marker each: `DQ-P1-DSTATUI-001` read-only candidate
  and exact provenance/CAS; `DSTATUI-002` EVALUATED PASS/FAIL and eligible
  target separation; `DSTATUI-003` explicit FAIL+VALID atomic decision;
  `DSTATUI-004` REVIEW_ONLY/INELIGIBLE/HELD target enforcement;
  `DSTATUI-005` stale, scope, forged-input, and safe-error boundaries; and
  `DSTATUI-006` restart/idempotent replay with zero duplicates.
- Expected files and consumers: a thin HTTP workflow Application facade, new
  data-review router, minimal API export/main factory wiring, focused temporary
  SQLite tests, and the parallel Korean Frontend client. No migration, Domain
  state extension, Scheduler, AI, default database, external API, real input,
  or output workbook change is planned.

## Canonical first-setup configuration API

- Trigger: the local UI can expose missing row bindings and missing approved
  Master evidence, but a user still needs a project-scoped, audited way to
  create the canonical hierarchy and complete each existing first-revision
  workflow without direct database work.
- Requirements and sources: bounded progress for the existing Phase-1
  configuration framework (`CFG-001`, `CFG-004`, `CFG-016~017`, `GOV-008`,
  `ING-023`) and UI contracts `DQ-P1-CFGUI-001~010`. This slice composes the
  already-persistent 0004 Master Configuration schema and command service; it
  adds no table, calculation, status, or inferred business value.
- Hierarchy boundary: explicit ADMIN commands create project-local Model,
  Supplier, ModelPart, and InspectionItem records. New items always begin as
  `CANDIDATE`; a separate CAS-protected ADMIN command explicitly changes that
  first decision to `MANAGED` or `EXCLUDED`.
- Master boundary: the browser supplies exact decimal strings, unit, external
  revision, source reference, effectivity, reason, and row versions. The
  server creates only revision 1 as `DRAFT`; separate trusted REVIEWER and
  ADMIN commands perform review and approval. No supplier value, tolerance,
  current date, unit conversion, or numeric default is copied or inferred.
- Binding boundary: selection data contains only APPROVED Mapping revisions
  and their exact persisted row keys/source coordinates. A first binding
  Draft must identify one exact Mapping scope and row, explicit source model
  values, canonical hierarchy, measurement mode, sample policy, effectivity,
  and reason. Separate trusted REVIEWER and ADMIN commands retain their own
  Audit records and CAS versions; the server generates the source reference
  from the persisted Mapping identity.
- Read and replay boundary: `GET /api/v1/configuration/snapshot` is project
  isolated and returns the canonical hierarchy, all first-revision workflow
  states, approved Mapping row selectors, immutable provenance hashes, CAS
  versions, and explicit capability limits. After restart, the existing
  approved/effective binding catalog is the only mechanism that can make the
  matching Long row loadable.
- Trust and safety: one trusted `LOCAL_OWNER` may possess both roles, but actor
  and roles are injected by the server and are never request fields. Direct
  Draft-to-Approved transitions, later revisions, supersession, fuzzy matches,
  automatic disposition, binding, Master creation, data-status promotion,
  official values, AI, and Scheduler behavior remain unavailable. Errors use
  stable Korean-safe codes without internal IDs or paths beyond the explicit
  opaque workflow provenance returned on success.
- Stable contracts, one marker each: `DQ-P1-CFGUI-001` project snapshot and
  hierarchy scope; `CFGUI-002` explicit hierarchy/supplier creation;
  `CFGUI-003` item disposition; `CFGUI-004` exact Master Draft;
  `CFGUI-005` separate Master review; `CFGUI-006` Master approval/catalog;
  `CFGUI-007` approved Mapping row selection and binding Draft;
  `CFGUI-008` separate binding review/approval; `CFGUI-009` restart catalog
  reuse by Long; and `CFGUI-010` stale/forged/cross-scope and no-auto-effect
  negative boundaries.
- Expected files and consumers: a thin configuration Application facade, new
  configuration router, minimal API export/main factory wiring, focused
  temporary SQLite tests, and the parallel Korean Frontend client. No
  migration, default database, live input, external API, output workbook,
  Scheduler, AI, `VALID` decision, or later-revision UI is planned.

## Durable Bulk staging and approved-Template variation review

- Trigger and source: Phase 2 begins with the bounded Bulk Import preparation
  described by Source 04 sections 5, 12, and 15 and Master Spec sections 9.6,
  9.9, and Phase 2. This slice stages several explicit project/supplier scoped
  `.xlsx`/`.xlsm` originals, reuses only a fresh exact approved Mapping, and
  reports variations and exceptions. It does not finalize or load data.
- Persistence: SQLite migration 0006 adds project-isolated append-oriented
  `bulk_import_batches` and `bulk_import_entries`. Each entry owns a
  server-reserved receipt identity and exact upload metadata so recovery after
  raw preservation replays the same receipt instead of creating another one.
  Batch idempotency is `(project_key, idempotency_key)` plus a canonical upload
  manifest; an exact retry replays and a changed manifest fails closed.
- State: batches move `STAGED -> PROCESSING -> COMPLETED` or
  `COMPLETED_WITH_EXCEPTIONS`/`FAILED`; entries move
  `STAGED -> PROCESSING -> TERMINAL`. Terminal outcomes are
  `CANDIDATE_READY`, `DUPLICATE_CANDIDATE`, `MAPPING_REQUIRED`, `SCAN_FAILED`,
  `IDENTIFIER_HOLD`, `BINDING_HOLD`, `VARIATION_REVIEW_REQUIRED`,
  `REVISION_REVIEW_REQUIRED`, or `ERROR`. Restart requeues nonterminal rows;
  terminal evidence and receipt history remain immutable.
- Reuse and comparison: raw bytes always go through the existing Original File
  Store and scanner. A fresh Mapping Preview and read-only Long candidate are
  rebuilt for every file. Same bytes retain distinct receipts and are labelled
  duplicate candidates. Same supplier/model/LOT with changed source evidence
  records typed item/spec/tolerance/method/sample/judgment/shipment/date,
  revision, part/section, or structure differences as review evidence only.
- API: `POST /api/v1/bulk/batches` accepts `project_key`, `supplier_scope`,
  `idempotency_key`, and repeated `workbooks`; project-scoped
  `GET /api/v1/bulk/batches/{batch_id}` polls the durable snapshot. Limits are
  server owned. Errors, issues, and evidence paths are safe workbook-logical
  values and never expose a filesystem path or raw exception.
- Safety/capabilities: there is no per-file approval or batch finalize route.
  `auto_long`, `auto_valid`, `auto_replaced`, `auto_revision`, and `ai_used`
  remain false. No Scheduler, external API, default database, real workbook,
  official calculation, deletion, or automatic revision decision is added.
- Stable contracts, one marker each: `DQ-P2-BULK-001` durable multi-file raw
  staging; `BULK-002` exact approved-Template candidate; `BULK-003` Mapping
  variation; `BULK-004` raw-preserved scan failure; `BULK-005` typed identifier
  and binding holds; `BULK-006` separate-receipt exact duplicate;
  `BULK-007` same-LOT revision-review evidence; `BULK-008` manifest
  idempotency; `BULK-009` restart/recovery and exact summary; and `BULK-010`
  0006 migration/default-DB isolation and no automatic downstream effects.

## Explicit Bulk candidate finalization and historical revision comparison

- Trigger and source: Phase 2 durable Bulk staging now preserves every raw
  Receipt and reports exact approved-Template candidates, but no user command
  can yet finalize the eligible batch set into the existing pending-only Long
  store. Source 04 sections 5, 12, and 15 and Master Spec sections 9.6, 9.9,
  10.2, and Phase 2 require explicit revision handling and historical
  traceability without automatic trust decisions.
- Read-only finalization candidate: project-scoped
  `GET /api/v1/bulk/batches/{batch_id}/finalization-candidate` reads only the
  durable 0006 proof. It performs no workbook parse and no write. The candidate
  deterministically selects every `CANDIDATE_READY` entry and preserves typed
  exclusion evidence for duplicate, variation, revision-review, Mapping,
  identifier, binding, scan, and error outcomes. Per-file normal selection is
  not exposed.
- Explicit asynchronous command: project-scoped
  `POST /api/v1/bulk/batches/{batch_id}/finalizations` requires the complete
  candidate digest, `confirmed=true`, and a nonblank reason. The server injects
  trusted `LOCAL_OWNER` ADMIN authority, persists the immutable command and
  entry plan with a same-transaction generic Audit record, and immediately
  returns `202`. A bounded single-process worker resumes durable pending entries
  after restart; workbook reconstruction and Long confirmation never execute in
  the HTTP request thread.
- Prepared checkpoint and materialization: the initial Bulk worker's single
  workbook scan stores a versioned, size-bounded, hash-protected checkpoint
  containing the complete scan snapshot, serialized Long candidate, and exact
  Mapping/binding proof digests. Polling never loads the full checkpoint.
  Finalization strictly decodes that checkpoint, reloads the approved Mapping
  and binding catalogs against the saved scan, reconstructs the Long candidate,
  and requires byte-for-byte canonical proof equality before calling the
  existing Long persistence boundary. It does not reopen or rescan Excel.
  Legacy 0006 candidates without a checkpoint are reported as
  `BULK_FINALIZATION_PREPARATION_REQUIRED` and cannot be finalized. Successful
  entries store only `PENDING`/`HELD`; partial progress commits per entry so a
  crash or retry cannot duplicate already-materialized Long rows.
- Persistence and migration: SQLite-bounded migration 0007 adds prepared
  checkpoint columns to Bulk entries, a bounded applied-Mapping proof on Long
  jobs, and project-composite `bulk_finalization_commands` and
  `bulk_finalization_entries`. Existing business payloads and Long rows remain
  unchanged; 0006 Long jobs receive only a derived proof after their complete
  candidate SHA is verified one row at a time. Physical proof columns stay
  nullable to avoid rebuilding the referenced SQLite job parent, while every
  migration/application write populates both fields and every reader rejects a
  null, partial, or digest-mismatched pair. Nonempty downgrade fails before DDL;
  fresh, upgrade, rollback, and default-database isolation are tested explicitly.
- Historical comparison: on-demand
  `POST /api/v1/history/comparisons` performs a zero-write standard-DB query for
  two explicit inspection-date ranges, explicit data-status set, and optional
  exact model, part, item, supplier, and Mapping revision filters. It returns
  bounded side-by-side LOT/result/sample raw values and evidence hashes plus the
  Mapping, binding, applied-Master, data-status, and historical judgment
  provenance stored with each result. Finalized data is queried from Long rows
  only; no workbook is reopened.
- Safety and capability limits: no per-file approval, automatic Long load,
  `VALID`, `REPLACED`, data-status decision, threshold, conversion, mean, Cpk,
  trend, AI, Scheduler, default database, or external call is introduced.
  Comparison reports only exact counts/revision sets and source evidence; it
  never rejudges historical data against the current Master.
- Stable contracts, one marker each: `DQ-P2-BULKFINAL-001` zero-parse/zero-write
  batch candidate; `BULKFINAL-002` explicit asynchronous batch-wide pending
  materialization; `BULKFINAL-003` exception exclusion and no per-file path;
  `BULKFINAL-004` stale/tamper/scope/confirmation fail-closed;
  `BULKFINAL-005` partial restart/idempotent zero-duplicate resume; and
  `BULKFINAL-006` 0007/Audit/default-database isolation. Historical query
  contracts are `DQ-P2-HIST-001` date/project isolation; `HIST-002` exact
  revision/status side-by-side evidence; `HIST-003` immutable historical
  judgment and Master provenance; and `HIST-004` bounded ranges/safe errors and
  explicit no-statistics behavior.
- Expected files and consumers: new finalization and historical-query
  Application/Infrastructure/API modules, SQLite migration 0007, minimal API
  factory/schema wiring, Korean finalization/comparison UI, focused temporary
  SQLite/File Store tests, living trackers, and one release Gate. Production
  Scheduler and the default data source remain outside this bounded slice.

## Explicit audited result-replacement chain

- Trigger and source: Source 04 sections 12 through 15, Master Spec sections
  8.2, 9.9, 22.3, and 22.6, plus `ING-039~043`, `GOV-005`, and `GOV-008`
  require same-LOT correction history without deleting the original decision.
  This bounded slice links two already-materialized Long results. Bulk
  `REVISION_REVIEW_REQUIRED` remains review evidence only and is not promoted
  automatically or by a new per-file shortcut.
- Atomic state boundary: the predecessor must be exactly `VALID` or `SUSPECT`.
  The successor must still be `PENDING`, and its freshly rebuilt existing
  data-review candidate must be `EVALUATED` and explicitly allow `VALID`.
  One trusted ADMIN transaction applies the ordinary successor
  `PENDING -> VALID` decision and the predecessor
  `VALID|SUSPECT -> REPLACED` transition together. There is therefore no
  observable double-`VALID` official-selector window. `FAIL + VALID` remains
  allowed under the existing approved-Master rule.
- Immutable evidence: the predecessor's original data-status transition,
  judgment, applied Master, raw values, source evidence, and Audit record stay
  unchanged. A separate replacement transition stores the exact pair, both
  result CAS bases, the successor data-status transition, and complete ordered
  predecessor/successor measurement evidence with before/after versions and
  digests. No sample-to-sample relationship is inferred and
  `superseded_measurement_id` remains unused.
- Persistence: SQLite-bounded migration 0008 adds project-composite
  replacement transition and measurement-evidence tables plus explicit
  replacement pointers on Long results/measurements. Uniqueness permits a
  linear `A -> B -> C` chain while preventing branches and merges. Existing
  0007 rows remain byte-for-byte unchanged. A downgrade with replacement
  history fails before DDL; fresh, upgrade, downgrade/re-upgrade, FK, rollback,
  metadata, and default-database isolation are required evidence.
- Read and command boundary: the candidate endpoint is deterministic and
  zero-write. It revalidates both immutable Long anchors, the predecessor's
  existing decision, the successor's complete data-review candidate, exact
  project/model/model-part/supplier/item/LOT identity, complete measurement
  sets, and current chain ends. The decision endpoint requires explicit
  confirmation, nonblank reason, candidate digest, and complete CAS receipt;
  actor, roles, statuses, command IDs, Master values, and judgments are
  server-owned. Exact retry replays; stale, tampered, cross-project,
  branch/merge/cycle, or conflicting intent fails closed.
- Audit and history: the same transaction retains the existing separate
  `DATA_STATUS_DECIDED` Audit for the successor and appends
  `RESULT_REPLACED` for the explicit pair. Historical comparison exposes the
  immutable original decision separately from a bounded, digest-verified
  replacement chain. Data-review replay accepts only a verified later
  replacement projection; it never rewrites the original transition.
- Safety and capability limits: no automatic Long load, replacement, trust
  decision, Master change, unit conversion, statistics, threshold, AI, mail,
  Scheduler, external call, default database, or source-file mutation is
  introduced. The official selector continues to read only rows whose current
  result and measurement states are both `VALID`.
- Stable contracts, one marker each: `DQ-P2-REPL-001` exact zero-write
  candidate; `REPL-002` successor evaluation and typed evidence delta;
  `REPL-003` atomic paired transition and immutable original evidence;
  `REPL-004` SUSPECT predecessor plus `FAIL + VALID` and no double official
  selection; `REPL-005` bounded `A -> B -> C` chain proof; `REPL-006`
  confirmation, stale, tamper, and scope rejection; `REPL-007` restart,
  idempotency, branch/merge/cycle rejection; `REPL-008` dual-Audit rollback;
  `REPL-009` replacement-aware original replay/history integrity; and
  `REPL-010` 0008 migration, downgrade, metadata, and default-DB isolation.
  Korean UI contracts are `DQ-P2-REPLUI-001` exact predecessor/successor,
  Master, measurement-set, and risk evidence plus a reason and explicit
  confirmation before the single ADMIN command; and `DQ-P2-REPLUI-002`
  ineligible-path disablement, pair/digest reload confirmation reset, and
  negative proof that no automatic `VALID`/`REPLACED`, statistics, threshold,
  AI, Issue, finalization, or coverage effect is exposed.

## Phase 2 initial database data-quality report

- Trigger and source: Source 04 section 15, Roadmap Phase 2, Master Spec
  sections 9.6 and Phase 2, and `ING-016`, `ING-039~045`, and `ING-050`
  require an exact post-Bulk inventory and unresolved-evidence report. This
  slice is a project-scoped read-only projection over the durable 0006~0008
  database truth; it is not an official baseline approval or Phase Gate.
- Read boundary: project-scoped
  `GET /api/v1/bulk/batches/{batch_id}/data-quality-report` reads one durable
  Bulk batch and its receipts, entries, Long jobs/lots/results/measurements,
  finalization proof, data-status decisions, and replacement links in bounded
  batch queries. It returns exact totals and all six current data-status
  counts, with deterministic canonical digests and bounded detail groups that
  separately expose total, returned, `has_more`, and a digest of the complete
  ordered evidence set.
- Evaluation truth: only evidence already persisted by implemented engines is
  reported as `EVALUATED`. Missing representative/approved real inputs are
  `BLOCKED_BY_INPUT`. Repeated-value, partial-duplicate, Outlier, and shipment
  cumulative-mismatch detection are future engines and must be reported as
  `NOT_EVALUATED_BY_PHASE`, never as a zero finding. The report creates no
  score, threshold, PASS/FAIL, official baseline, Gate, or inferred quality
  conclusion.
- Integrity and failure paths: stored JSON, digests, project-composite links,
  finalization snapshots, data-review transitions, and replacement chains are
  verified before use. Malformed query input is a safe 400, missing or
  cross-project batches are 404, persisted evidence conflicts/tamper are 409,
  and unavailable persistence is 503; no internal path or raw exception is
  returned.
- Expected files and consumers: new data-quality-report Domain/Application/API
  modules, minimal main/router wiring, and isolated temporary-SQLite contract
  tests. There is no migration, table, worker, workbook open, source-file
  mutation, frontend change, AI/Scheduler/external call, or default-database
  access. Existing Bulk/finalization/history/replacement APIs remain unchanged.
- Stable contracts, one marker each: `DQ-P2-DQREPORT-001` exact project/batch
  inventory; `DQREPORT-002` all data-status counts and official-selection
  boundary; `DQREPORT-003` Bulk outcome and unresolved proof; `DQREPORT-004`
  finalization/materialization proof; `DQREPORT-005` immutable replacement
  proof; `DQREPORT-006` explicit evaluation-scope states; `DQREPORT-007`
  bounded deterministic details/digest and no N+1; and `DQREPORT-008`
  read-only, tamper/scope/safe-error and prohibited-capability boundaries.
