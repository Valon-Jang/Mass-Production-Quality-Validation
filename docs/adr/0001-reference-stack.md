# ADR 0001: Windows-first local reference stack

- Status: Accepted for Phase 0
- Date: 2026-08-15
- Requirements: GOV-010, GOV-011, GOV-012, ARC-020

## Context

Mass Production Quality Validation is an optional, single-user, local application that may manage more
than one personal project. It will later be distributed as a Cloud Scheduler
extension pack, but its code, database, source-file store, versions, and
failure boundary remain independent. Phase 5 is the earliest point for a live
Scheduler or Outlook integration.

The workstation has Python 3.12 x64, Node.js, npm, and Git. It does not have
Docker, make, pnpm, uv, Poetry, or a global Ruff installation. Bare `python`
points to Python 3.9 x86 and is not safe for this repository.

## Decision

- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic.
- Phase 1 workbook access: openpyxl in read-only use; VBA is never executed.
- Development data: SQLite and a local immutable-by-policy original file store.
- Commands: PowerShell scripts with an npm command facade.
- Python execution: always `.venv\Scripts\python.exe`; no global installs.
- Bind development HTTP only to `127.0.0.1`.
- AI and Scheduler use provider-neutral ports; live adapters remain disabled.
- Frontend is added with the first Mapping Preview UI rather than as an empty
  Phase 0 stub.

The application-level integration inbox will be common to all projects. The
long-term physical choice between a common catalog plus project databases and
a single project-keyed database is intentionally deferred until the Project
Registry vertical slice. No Phase 0 migration may silently lock that choice.

## Alternatives

- Docker/PostgreSQL now: rejected because it adds operating burden without
  evidence from data volume or concurrency.
- A shared server now: rejected because the confirmed initial use is personal
  and opt-in.
- Direct Scheduler database access: prohibited; use a versioned contract.
- A permanent message broker: rejected until operating need is demonstrated.

## Transition boundaries

PostgreSQL, shared/object storage, corporate authentication, server deployment,
and a live AI/Scheduler adapter may be added behind existing ports. Each needs
its own impact analysis, migration, compatibility tests, and rollback.

