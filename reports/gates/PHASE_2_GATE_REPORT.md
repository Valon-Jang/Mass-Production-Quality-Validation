# Phase 2 gate report

- Status: `IN_PROGRESS`
- Date opened: 2026-08-15
- Durable historical-OQC Bulk staging/review slice: `PASS`
- Explicit batch-wide pending finalization slice: `PASS`
- Bounded historical evidence comparison foundation: `PASS`
- Bounded explicit paired ADMIN result-replacement slice: `PASS`
- Actual representative historical acceptance: `BLOCKED_BY_INPUT`

## Implemented bounded slice

- An approved Mapping can start one durable project/supplier-scoped batch of
  `.xlsx`/`.xlsm` sources. Every submission retains its own Receipt, hash, and
  safe exception evidence.
- Alembic `0006` persists batch/entry staging, manifest idempotency, duplicate,
  variation, revision, Mapping, identifier, binding, scan, and system outcomes.
- The first successful scan stores a bounded, SHA-verified preparation
  checkpoint. Finalization does not reopen or rescan the workbook.
- Alembic `0007` persists a server-derived immutable finalization plan and its
  per-entry progress. One explicit batch-wide confirmation with reason may
  materialize eligible entries as Long `PENDING`/`HELD` only.
- Mixed batches materialize only eligible entries. Duplicate, revision-review,
  variation, Mapping, scan, and other exceptions remain excluded with their
  original outcome and evidence; there is no per-file approval action.
- Finalization is durable, restartable, and idempotent. Request decisions use a
  trusted local-owner Audit identity; background outcomes use a system Audit
  identity. A command completes only for terminal `COMPLETED_PENDING` or
  `REUSED` Long jobs.
- A read-only comparison endpoint and Korean UI compare two explicit date
  windows using stored Receipt, source Cell/raw, Mapping, Binding, applied
  Master, decision, and measurement evidence. Output is bounded and reports
  structural counts and exact revision sets only.
- Applied-time Mapping and Master evidence remains readable after later
  supersession. Current revision periods are shown separately and never rewrite
  the historical decision.
- Alembic `0008` persists an append-only, project-scoped replacement link plus
  complete ordered predecessor/successor measurement sets and their digests.
  The operation is one confirmed ADMIN transaction: an eligible
  `VALID`/`SUSPECT` predecessor and all its measurements become `REPLACED`, and
  one reviewed `PENDING` successor and all its measurements become `VALID`.
- Candidate and decision endpoints fail closed on stale, forged, cross-project,
  branch, merge, or cycle evidence. Exact replay is durable, including an
  earlier link after the successor is replaced again, and paired Audit failure
  rolls back both sides.
- The Korean historical screen shows the two selected identities, immutable
  Mapping/Master/source proof, bounded measurement samples, full-set digests,
  change/risk evidence, reason, and explicit confirmation. Replacement history
  is displayed separately from the original data-status decision.

## Negative boundary

- No automatic finalization, `VALID`, `REPLACED`, Mapping/Master approval,
  official calculation, statistics, trend, threshold, current-Master
  rejudgment, AI call, email, Scheduler access, or external write. `REPLACED`
  is reachable only through the explicit audited ADMIN pair command above.
- Durable Bulk revision candidates remain review-only and cannot invoke that
  pair command or select the official successor automatically.
- A completed finalization means only that the eligible set reached pending
  Long storage. Any excluded entry keeps the initial historical-database Gate
  incomplete.
- Legacy `0006` entries without a verified preparation checkpoint remain
  `BULK_FINALIZATION_PREPARATION_REQUIRED` and are not rescanned implicitly.
- Source workbooks remain read-only; internal paths and raw exception messages
  are not exposed through the API or UI.

## Automated evidence

- Full release Gate: `PASS` on 2026-08-16.
- Backend: 193 passed; 190 unique stable contract IDs; skip/xfail/xpass 0.
- Frontend: 6 files / 30 tests passed; TypeScript typecheck and Vite production
  build passed.
- Static and governance: compile, Ruff/format (116 files), strict mypy
  (80 sources), requirement integrity (`333 + 13`, scope/status `93 / 84`),
  and migration graph `0001 -> 0008` passed.
- Required Phase 2 IDs: `DQ-P2-BULK-001..010`,
  `DQ-P2-BULKUI-001..004`, `DQ-P2-BULKFINAL-001..006`,
  `DQ-P2-BULKFINALUI-001..003`, `DQ-P2-HIST-001..004`, and
  `DQ-P2-HISTUI-001..002`, plus `DQ-P2-REPL-001..010` and
  `DQ-P2-REPLUI-001..002`.
- Focused finalization/history result: 10/10 passed. The affected legacy
  fixtures and API/lifecycle paths passed after their preparation-proof fixture
  contract was updated.
- The final full Gate includes all ten replacement contracts and both mirrored
  replacement-UI contract IDs. The affected data-review, history, migration,
  and application-lifecycle evidence is 66/66 green after the two execution-
  discovered state/migration defects and one stale parity expectation were
  corrected at their exact failing nodes.
- Local browser QA used the actual Korean historical/replacement component with
  isolated synthetic API evidence at 1440x1000 and 390x844. It verified no page
  horizontal overflow, NG-to-PASS risk display, disabled confirmation before
  reason/check, atomic completion wording, successor-only official selection,
  reload confirmation reset, and zero console warning/error. This is UI
  framework evidence, not installed-package or real-Golden acceptance.
- The only warnings were one upstream Starlette/httpx transition warning and
  five Python 3.12 SQLite datetime-adapter deprecation warnings; no functional
  warning or failure was accepted.
- Rebuilt Windows extension artifact: version `0.1.0`, 85 files, 451,260 bytes,
  SHA-256
  `0A25F47469036FDF4B61BE44B32340F103FF45D8E17BB3AFD736187E64306F5C`;
  the embedded inventory verifies as
  `6C315F0BFEBFC367BD2B9015F52EC34D45F87A9EEEB36253A89BA6130B29442D`.

## Migration and resilience

- `0007` adds the prepared checkpoint, bounded applied-Mapping proof, and
  finalization command/entry tables while preserving prior Audit, Mapping,
  Long, Master, data-status, and Bulk records.
- Existing Long candidates are backfilled only after their full canonical JSON,
  candidate SHA, Receipt, content hash, and effectivity are verified. Null,
  partial, or digest-mismatched projections fail closed.
- SQLite upgrade/downgrade avoids rebuilding the referenced ingestion-job
  parent table. Foreign-key checks, metadata parity, non-empty finalization
  downgrade refusal, prior-row preservation, restart, queue refill, partial
  resume, stale CAS, Audit rollback, and zero-duplicate replay are covered on
  explicit temporary databases.
- Candidate reads do not hydrate workbook-scale checkpoint/candidate payloads;
  a worker loads at most one claimed checkpoint. Historical reads use bounded
  scalar projections and project-composite provenance checks.
- `0008` uses a foreign-key-safe SQLite migration, preserves existing
  data-status history without synthetic backfill, verifies referential
  integrity, and refuses a lossy downgrade when replacement history exists.
  Replacement writes preserve immutable source, Master, judgment, and original
  decision evidence while storing exact full measurement-set proof separately.

## Blocked or deferred

- User-accepted representative OQC plus two or three real historical files.
- Approved company Master/qualitative rules and Golden 100% comparison.
- Actual corrected-file Golden comparison and company acceptance of the
  replacement evidence shown by the bounded workflow.
- User-facing later Mapping/Master/Binding revision or supersession commands.
- Statistical baselines, control limits, trend, Cpk/Ppk, thresholds, export,
  and other later-phase analytics.
- PostgreSQL migration portability.
- Scheduler, Outlook, and live Qwen integration remain at their existing phase
  and input boundaries.

## Resume point

Run the representative historical-file and corrected-file acceptance when real
inputs are available. Keep the Phase 1 Golden Gate and Phase 2 overall Gate open
until that evidence exists; do not add statistical or business thresholds
before their approved inputs and later phase.
