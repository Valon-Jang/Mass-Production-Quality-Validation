# Mass Production Quality Validation implementation status

## Current state

- Baseline date: 2026-08-15
- Immutable baseline requirements: 333
- Post-baseline Living amendments: 13
- Current Living requirement universe: 346 (`333 + 13`)
- Planning preparation (legacy Roadmap Phase 0): complete
- Implementation foundation Phase 0: `PASS`
- Phase 1 Data Engine: `IN_PROGRESS`
- Phase 1 File Store -> Workbook Scanner framework slice: `PASS`
- Phase 1 Mapping Template/Preview framework slice: `PASS`
- Phase 1 persistent Mapping approval/Audit framework slice: `PASS`
- Phase 1 canonical Store -> Scan -> Mapping route slice: `PASS`
- Phase 1 deterministic pending Long-candidate slice: `PASS`
- Phase 1 pending-only Long persistence and Source Cell slice: `PASS`
- Phase 1 offline assumed-Qwen Mapping-location candidate slice: `PASS`
- Phase 1 persistent canonical hierarchy/row-binding/Master Spec framework
  slice: `PASS`
- Phase 1 approved-Master review and explicit data-status decision slice:
  `PASS`
- Phase 1 Korean local manual-intake UI/API slice: `PASS`
- Phase 1 durable Receipt -> Mapping review/approved Preview UI slice: `PASS`
- Phase 1 receipt-bound Mapping Draft/Reviewer/Admin UI slice: `PASS`
- Phase 1 Receipt -> Long candidate/explicit pending confirmation UI slice:
  `PASS`
- Phase 1 explicit data-status review/Admin decision UI slice: `PASS`
- Phase 1 canonical hierarchy/row-binding/Master first-setup UI slice: `PASS`
- Phase 1 Windows personal extension package framework slice: `PASS`
- Phase 2 durable historical-OQC Bulk staging/review slice: `PASS`
- Phase 2 explicit batch-wide pending finalization slice: `PASS`
- Phase 2 bounded historical evidence comparison foundation: `PASS`
- Phase 2 bounded explicit paired ADMIN result-replacement slice: `PASS`
- Current release Gate: `PASS` at Alembic `0008` with 193 backend tests,
  190 stable contract IDs, 30 frontend tests, and static/runtime skip 0.
- Phase 5 Scheduler integration: `DEFERRED_BY_PHASE`

## Active scope

1. Preserve the passed Phase 0 Windows bootstrap, migration, audit/identity,
   requirement-integrity, and release Gate contracts.
2. Preserve the passed Phase 1 Original File Store, deterministic Workbook
   Scanner, and canonical manual Store -> Scan route contracts.
3. Preserve Long DB and exact Source Cell evidence contracts. Ingestion remains
   `PENDING`/`HELD`; only a trusted Admin command may explicitly decide an
   eligible result and all measurements as `VALID`, `SUSPECT`, or `EXCLUDED`.
4. Preserve the Korean synthetic OQC -> Scanner -> review-only offline AI
   contract while keeping live Qwen calls, secrets, approval, and official
   decisions outside Phase 1.
5. Preserve the persistent canonical hierarchy, approved numeric Master,
   deterministic review, historical provenance, and explicit data-status
   decision boundary without inventing real business values or auto-promoting
   `VALID` data.
6. Preserve the actual representative workbook acceptance as
   `BLOCKED_BY_INPUT`; synthetic evidence cannot close the Golden Gate.
7. Preserve the Korean exact-role Mapping, canonical first setup, Long
   candidate/confirmation, and explicit data-status decision flow together with
   the installed personal-v1 evidence.
8. Preserve the explicit batch-wide finalization and bounded two-period source
   evidence comparison. Finalization may create only `PENDING`/`HELD`; the
   comparison remains read-only and performs no statistics or rejudgment.
9. Preserve the separate explicit paired replacement boundary. Only a
   confirmed ADMIN command may atomically move one eligible `VALID`/`SUSPECT`
   predecessor to `REPLACED` and one reviewed `PENDING` successor to `VALID`.
   Bulk revision candidates remain review-only, and no replacement, statistics,
   threshold, or AI decision is automatic.

## User-confirmed decisions after the baseline

- Mass Production Quality Validation is an optional personal Cloud Scheduler extension pack.
- One local installation may manage multiple isolated projects.
- Scheduler gathers OQC mail references in one place; Mass Production Quality Validation determines the
  project from workbook evidence.
- Scheduler stores the final Outlook Mail Locator after its classification or
  move step. Mass Production Quality Validation does not search the entire mailbox.
- Scheduler owns the one-time AI endpoint and API-key input. Mass Production Quality Validation consumes
  a versioned provider profile without copying plaintext secrets.
- The eventual cloud model is `Qwen3.5-33B`. The current workspace cannot call
  it, so Phase 1 verifies only an offline, provider-neutral, strict response
  contract with `runtime_verified=false`.
- The user is the acceptance owner for identifier, specification, shipment,
  raw-measurement, and source-cell comparison.

The approved decisions and implementation-safety contracts are tracked
separately from the immutable baseline in
`requirements/LIVING_REQUIREMENTS_AMENDMENTS.csv`. Their current Gate placement
is narrowed in `requirements/PHASE_0_1_GATE_SCOPE.csv`, and implementation
evidence is overlaid by `requirements/LIVING_IMPLEMENTATION_STATUS.csv`; no
baseline CSV row was rewritten.

## Integration boundary

- The Scheduler developer request is
  `docs/integration/CLOUD_SCHEDULER_MASS_PRODUCTION_QUALITY_VALIDATION_EXTENSION_REQUEST.md`.
- DQ-side documentation, provider-neutral ports, DTOs, and mock/in-memory paths
  are allowed before Phase 5.
- Outlook live fetch, Queue polling, Scheduler AI Secret resolution, Scheduler
  discovery/compatibility integration, and any Scheduler Workspace change
  remain `DEFERRED_BY_PHASE`. A Scheduler-independent local package now exists.
- No Phase 0/1 evidence may be used to declare the real Scheduler contract
  accepted.

## Blocked by input

- Representative OQC workbook and two or three same-format historical files.
- Applicable Master Spec or approved acceptance criteria.
- Outlook provider type and actual Mail Locator/fetch contract.
- Cloud Scheduler installer/version/hash and extension discovery contract.
- Windows Secret Store mechanism, ACL, and shared AI profile handoff.
- Segregated live `Qwen3.5-33B` endpoint acceptance for accuracy, latency,
  context limits, and OpenAI-compatible behavior.
- Approved statistical thresholds and corporate export template for later
  phases.

## Current implementation constraints

- The Original File Store is deliberately local and single-process. Its lock
  does not provide cross-process exclusion; a durable claim/lock must precede
  any multi-process Worker or server deployment.
- A deterministic current-user Windows extension ZIP now carries the exact
  runtime source, migrations, prebuilt Frontend, hashed dependency lock,
  launcher, and per-file inventory. Install/update/remove use disjoint code and
  data roots with rollback and data-preserving removal. It creates no registry,
  autostart, service, or Scheduler state. Python 3.12 plus package-index/cache
  access is still required because this slice has no embedded runtime or
  offline wheelhouse; Scheduler compatibility remains unverified.
  The current `0.1.0` artifact has 85 files, is 451,260 bytes, and has SHA-256
  `0A25F47469036FDF4B61BE44B32340F103FF45D8E17BB3AFD736187E64306F5C`.
  This rebuilt artifact passed inventory verification after the automated
  release Gate but has not received
  a separate installed-browser smoke. An earlier `0.1.0` installed build's
  isolated fresh install, localhost health, Korean desktop render, actual
  three-sheet OQC intake, and first configuration write are recorded in
  `reports/acceptance/2026-08-15-personal-v1-installed-smoke.md`. The earlier
  update/data-preservation and 390px acceptance remains recorded separately in
  `reports/acceptance/2026-08-15-windows-extension-installed-smoke.md`.
- The Mapping UI rehydrates the durable Receipt rather than a process-local job,
  rescans and checks name/size/SHA, loads a fresh project catalog, and displays
  all approved v1/v2 roles or paged raw/cached/formula/display source cells.
  Supplier scope is explicit and AI remains `NOT_CALLED`. The UI can now create
  a server-validated first schema-v2 Draft, record a separate Reviewer decision,
  and record a separate Admin approval before reloading the exact Preview. It
  cannot append later revisions or confirm pending Long persistence yet.
- Excel display text is not fabricated. Stored value, formula, cached value,
  and number format are separated, while true rendered display remains
  `NOT_RENDERED` until an approved rendering strategy exists.
- Synthetic workbook results prove framework behavior only and are not Golden
  acceptance evidence.
- Offline AI requests contain only bounded untrusted text/scalar structure and
  opaque exact Cell tokens. Responses are source-location hints only. They
  cannot approve/persist Mapping, calculate, create Spec, or produce an
  official judgment. Prompt injection, stale digest, malformed output,
  timeout, and provider failure fail closed without changing the Core path.
- The supported Mapping command path persists immutable revision payloads,
  review/approval/supersession decisions, resolved effectivity, row-version
  checks, and Audit. Existing direct `APPROVED` dataclass construction is only
  an in-memory framework/fixture compatibility path. Production authentication,
  UI commands, and retirement remain unimplemented.
- The bounded Mapping model handles row-oriented Cell mappings with optional
  roles and variable samples. Shared merged anchors, common cells reused by
  multiple points, Cell ranges, and column-oriented formats remain unsupported.
- Worksheet and ChartSheet structures are fingerprinted explicitly; a
  ChartSheet correctly has no fabricated used range.
- Mapping Preview verifies a caller-supplied project scope; workbook-driven
  project routing is still an upstream `ARC-025` responsibility.
- Canonical manual intake binds Receipt -> Scan -> Mapping Preview evidence and
  preserves raw input on every hold/failure path. The follow-up Long
  persistence layer provides receipt-scoped durable jobs, exact replay, and
  successful pending-materialization reuse; the local File Store lock itself
  remains process-local and there is no Worker/server claim loop.
- Persistent Mapping catalogs are immutable materialized snapshots. They remain
  usable after the loading session closes, but later approval changes require
  an explicit reload before another route lifetime begins.
- Alembic `0003` persists project-scoped source files, scan-sheet snapshots,
  ingestion jobs, pending OQC lots, inspection candidates, and exact
  measurement-cell evidence. Database constraints allow only `PENDING` or
  `HELD`, keep standardized values and system judgments null, and prevent
  unresolved bindings from populating canonical IDs.
- Long materialization is separate from raw preservation. Fatal materialization
  failures roll back lot/result/measurement rows while preserving the source
  and a durable failed job. Processing or failed owners are not silently reused;
  another receipt becomes `RECOVERY_REQUIRED`.
- Canonical Model -> ModelPart -> InspectionItem identities and an independent
  Supplier axis now persist under project-composite foreign keys. Items begin
  as `CANDIDATE`; audited Admin commands explicitly choose `MANAGED` or
  `EXCLUDED`. This is a bounded identity/configuration framework, not a full
  reusable Part catalog or supplier part-name alias system.
- Numeric Master Spec and exact supplier-scoped row bindings now have
  persistent Draft -> Reviewer-reviewed -> Admin-approved/superseded workflows,
  immutable payload digests, declared/resolved effectivity, row-version CAS,
  deterministic as-of catalogs, and atomic Audit rollback. Supplier OQC Spec
  evidence cannot populate Master automatically. Approved numeric limits are
  now compared deterministically and explicit data-status decisions preserve
  the applied historical revision. Actual company values, qualitative rules,
  and configured unit conversions remain unimplemented.
- The Korean synthetic baseline now passes the complete supported canonical
  path: File Store -> Scanner -> persisted approved Mapping -> pending Long
  candidate -> isolated SQLite persistence -> restart/exact replay. It maps six
  identifiers, six rows, and 48 sample cells; changed, ambiguous, and error
  forms preserve raw evidence and remain held. This is synthetic framework
  acceptance and does not make the workbook Golden.
- Mapping schema v2 now preserves explicit source roles for part name,
  production date, current/cumulative shipment quantity, section/category,
  unit, measurement point/location/cavity, Target, LSL, USL, and source Spec
  revision through Preview and pending Long persistence. Schema-v1 payload and
  replay hashes remain fixed. These are source claims only; no shipment
  arithmetic, unit conversion, Spec evaluation, or official value is created.
- The v2 role vocabulary remains a row-oriented Cell-mapping framework. A
  shared Section/Spec/Unit anchor reused by several rows, merged common cells,
  Cell ranges, repeating selectors, and multi-model/LOT grouping still require
  later Mapping extensions.
- The `0003` migration is verified for the current SQLite deployment. Its
  partial owner index and Boolean checks require a dedicated portability
  migration before PostgreSQL is supported; see
  `docs/adr/0002-pending-long-persistence-boundary.md`.
- Alembic `0004` adds only canonical identity, numeric Master Spec, and exact
  row-binding workflow tables plus Mapping-scope support indexes. Fresh install,
  `0003 -> 0004`, downgrade/re-upgrade, metadata parity, and exact preservation
  of prior Audit/Mapping/Long row IDs, states, raw evidence, and SHA values pass
  on explicit temporary SQLite URLs. The default workspace database was not
  upgraded by this test slice.
- Alembic `0005` adds the append-only data-status transition and current result
  decision projection, expands the trust-state vocabulary, and keeps initial
  Long materialization restricted to `PENDING`/`HELD`. A review candidate is
  read-only; only an Admin command can atomically update one result and all its
  measurements. Spec `FAIL + VALID` is supported, source-unit mismatch blocks
  `VALID`, and official reads require both result and measurement `VALID`.
  Fresh/upgrade/failure-recovery/safe-downgrade tests use explicit temporary
  SQLite URLs. Terminal-history downgrade fails before mutation. Ordinary
  single-result data-status review still cannot create `REPLACED`; replacement
  is available only through the separate paired command described below.
- Alembic `0006` adds project-scoped durable Bulk batches and entries only. A
  batch keeps its idempotent manifest, reserved Receipt identity, bounded
  Mapping/Long proof, duplicate/revision links, safe exception evidence, and
  restart state. Payload/digest pairs fail closed, non-empty downgrade is
  refused, and prior tables are unchanged. The worker performs one scan in a
  successful initial attempt and after a transient Receipt-link retry; a process
  crash immediately after scan may rescan during recovery because a durable scan
  snapshot is not part of this staging-only slice.
- Alembic `0007` adds a durable one-time prepared scan/Long checkpoint, a
  bounded SHA-verified applied-Mapping projection, and project-scoped
  finalization command/entry history. An explicit whole-eligible-set command is
  restartable and idempotent, uses separate LOCAL_OWNER request and SYSTEM
  outcome Audits, and can complete only with `COMPLETED_PENDING` or `REUSED`
  Long jobs. Legacy or tampered rows remain preparation-required. Physical
  applied-proof columns stay nullable solely to avoid rebuilding a referenced
  SQLite parent during upgrade/downgrade; migration backfill and every
  application write populate both fields, and readers reject null/partial or
  digest-mismatched proof.
- Alembic `0008` adds an append-only paired result-replacement chain and its
  complete ordered measurement-set evidence. A confirmed ADMIN command performs
  one atomic predecessor `VALID`/`SUSPECT` -> `REPLACED` and successor
  `PENDING` -> `VALID` transition, with row-version checks, immutable source and
  decision proof, paired Audit records, exact replay, and branch/merge/cycle
  rejection. The API and Korean historical screen expose bounded samples plus
  count/full-set digests. This does not approve a Bulk revision candidate,
  calculate a result, call AI, or choose a correction automatically.
- Historical comparison reads bounded scalar projections and exact stored
  source/measurement evidence from the Long DB. It keeps applied-time Mapping,
  Binding, Master, and data-status decision proof distinct from current
  revision periods, caps results/samples/raw values, and returns structural
  counts and revision sets only. It also returns a separately labeled bounded
  replacement chain without rewriting the original decision. It does not
  hydrate workbook-scale candidate snapshots, calculate trends, or apply
  current-Master rejudgment.
- Bulk issue output is capped at 200 entries with 4 KiB per compared value and a
  full-list digest/count marker. Revision evidence is capped at 2 MiB. The API
  returns proof summaries rather than the stored comparison payload, and never
  exposes an internal path or exception message.
- Direct Alembic execution requires an explicit database URL. This safeguard
  was added after the default development DB was unintentionally rebuilt during
  a read-only audit; see
  `reports/incidents/2026-08-15-default-dev-db-migration-audit.md`.
- After independent `0003` review, the known empty post-incident development DB
  was hash-backed up and upgraded through the hardened Bootstrap. Read-only
  verification confirms head `0003`, `quick_check=ok`, and zero rows in prior
  and new tables; this does not resolve the absence of a pre-incident backup.

## Reference evidence added

- Public PDF observation:
  `docs/references/2026-08-15-public-oqc-report-observations.md`.
- User-provided synthetic workbook evidence:
  `docs/references/2026-08-15-mass-production-quality-validation-oqc-demo-workbook-evidence.md`.
- Korean synthetic OQC and assumed-Qwen contract evidence:
  `docs/references/2026-08-15-korean-oqc-ai-mapping-samples.md`.
- The demo workbook exposed and now protects OPC extension-default workbook
  content-type handling. It also passed a bounded read-only Mapping Preview,
  but its own provenance explicitly prevents Golden classification.

## Resume point

The Korean local application now reaches durable Receipt replay, exact-cell
Mapping Draft/Review/Approval, user-facing canonical hierarchy/first numeric
Master/first row-binding setup, Long candidate inspection and explicit
`PENDING`/`HELD` confirmation, then read-only Master comparison and explicit
Admin data-status decisions without direct database work. The personal
extension and isolated installed-browser smoke pass. Phase 2 now also stages a
durable multi-file historical batch, separates duplicate/variation/revision and
failure evidence, persists the one-time scan basis, materializes the immutable
eligible set only after an explicit batch-wide confirmation, and provides a
bounded two-period raw/provenance comparison. A separate explicit ADMIN flow
now records a bounded, audited `REPLACED` chain for a selected eligible
predecessor/successor pair; it does not consume or approve Bulk revision
candidates automatically. Resume with actual representative historical-file
and corrected-file acceptance while
keeping Phase 1 Golden acceptance open for the real representative workbook and
approved company criteria. Do not add statistics or thresholds before their
approved inputs and later phase. Live Qwen, Scheduler discovery, later
Mapping/Master supersession UI, and actual Golden acceptance remain open or
blocked by their stated phase/input boundary.
