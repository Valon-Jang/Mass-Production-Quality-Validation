# ADR 0002: pending-only Long-format persistence boundary

- Status: Accepted for the bounded Phase 1 framework
- Date: 2026-08-15
- Requirements: ARC-007, ARC-008, ARC-026, ARC-030, ING-002, ING-029,
  ING-034, ING-035, ING-045, ING-046, ING-047, ING-053, CFG-017

## Context

Mass Production Quality Validation must retain exact source evidence in a standard Long-shaped database,
but no representative supplier Golden Workbook, approved canonical item
dictionary, Master Spec, unit configuration, or user validation decision is
available yet. Persisting rows as official `VALID` measurements would therefore
invent approval that the current evidence cannot support.

Raw File Store preservation must also remain independent from scan, Mapping,
Long materialization, and later cache transactions. Repeated delivery of the
same bytes must retain separate receipt history without duplicating a completed
pending materialization or reusing results across projects or parser contracts.

## Decision

- Alembic `0003` introduces project-scoped `source_files`, `source_sheets`,
  `ingestion_jobs`, `oqc_lots`, `inspection_results`, and `measurements` tables.
- The schema accepts only `PENDING` and `HELD` data. Standardized values and
  official system judgments are prohibited by database constraints.
- A persisted approved Mapping revision is loaded and used to rebuild the
  Mapping Preview before a job is claimed. The supplied Preview and Long
  candidate must match their immutable source and binding evidence exactly.
- Source/job claim commits before Long materialization. A fatal materialization
  error rolls back lot/result/measurement rows, then records a durable failed
  job without rolling back the File Store receipt.
- Exact receipt replay is idempotent. A distinct receipt may reuse only a
  terminal successful pending materialization. Processing or failed owners
  yield an explicit recovery state rather than an automatic takeover.
- Reuse identity includes project, source SHA-256, persisted Mapping revision
  and payload, canonical-binding selection, loader version, and Scanner contract
  version. A parser-contract change therefore creates a new materialization.
- Unapproved, ineffective, ambiguous, or scope-conflicting canonical bindings
  retain their full evidence as `HELD` while canonical ID columns remain null.

## Consequences and transition boundaries

This database is an auditable staging boundary, not the official statistical
baseline. A later, separately reviewed workflow must persist and approve the
canonical model/part/item dictionary, Master Spec, unit/method configuration,
and user validation decision before any promotion beyond `PENDING`/`HELD`.

The current migration and concurrency tests target the selected Phase 1 SQLite
deployment. PostgreSQL support remains an architecture boundary: dialect-
specific partial indexes and Boolean check expressions must receive a dedicated
migration compatibility review and tests before a PostgreSQL adapter is
declared supported.

No API, UI, Worker loop, Scheduler/Outlook adapter, AI call, automatic retry,
calculation, PASS/FAIL judgment, or cache update is part of this decision.
