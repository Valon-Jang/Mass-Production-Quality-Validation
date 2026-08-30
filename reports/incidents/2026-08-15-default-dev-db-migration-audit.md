# Incident: unintended default development database rebuild

- Date: 2026-08-15
- Affected path: `.localdata/mass_production_quality_validation.sqlite3`
- External systems affected: none
- Original ZIP and workbook affected: none

## Record

Symptom -> A read-only migration audit reported that its temporary database URL
was not set and that `alembic downgrade base` followed by `upgrade head` had run
against the default workspace development database.

Actual cause -> A PowerShell environment-variable command failed, but the
multi-command diagnostic continued. `alembic.ini` still contained a writable
default URL, so Alembic selected `.localdata/mass_production_quality_validation.sqlite3`.

Wrong initial judgment -> The audit assumed the temporary URL setup had taken
effect and did not fail closed or inspect and back up the resolved target before
a destructive downgrade.

Resolution -> Further writes were stopped. Read-only inspection found a valid
SQLite database at Alembic head `0002`, with zero rows in `audit_log`,
`mapping_template_histories`, `mapping_template_revisions`, and
`mapping_template_supersessions`. `PRAGMA quick_check` returned `ok`; journal
mode was `delete`; no backup, WAL, journal, or second application database was
found in the workspace. Post-incident SHA-256 is
`93C2FC1636B296B7BBE55D8D1D6198DD6E69F97CF43626C5FC29B8C8C36FDFB5`.

The prior row count cannot be proven because no pre-operation snapshot was
taken. Available implementation evidence strongly indicates the database was a
bootstrap-only empty development database: the application exposes no business
write API, existing Audit tests use temporary databases, and Mapping
persistence tests also use temporary databases. This inference is not a
recovery substitute.

Permanent regression test -> `DQ-P0-MIG-002` requires a direct Alembic command
to receive an explicit environment or programmatic database URL. The repository
configuration now contains a non-connectable sentinel, while Bootstrap sets and
then restores the intended local development URL explicitly. A failed temporary
URL setup therefore stops before opening any workspace database.

The hardened Bootstrap was then executed successfully. It left the database at
`0002`, restored `MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL` to the unset state, preserved all zero
row counts, and left the post-incident database SHA-256 unchanged.

## Subsequent controlled `0003` upgrade

After the pending-only Long migration passed independent review and temporary-
database tests, the workspace database was inspected through SQLite read-only
mode. It was still at `0002`, `PRAGMA quick_check` returned `ok`, the recorded
Audit/Mapping tables remained at zero rows, and its SHA-256 was still
`93C2FC1636B296B7BBE55D8D1D6198DD6E69F97CF43626C5FC29B8C8C36FDFB5`.

Before upgrade, an exact 86,016-byte copy was created at
`.localdata/backups/mass_production_quality_validation-before-0003-20260815.sqlite3`; source and backup
SHA-256 values were verified identical. The hardened Bootstrap then supplied
the explicit local URL and performed only `0002 -> 0003`.

Post-upgrade read-only verification found `PRAGMA quick_check=ok`, Alembic head
`0003`, all prior Audit/Mapping tables still at zero rows, all six new Long
tables present at zero rows, and database SHA-256
`451CB094007E1417DBC8480048E21F9B89E006E7FBA15252F37D9CCED79CA28C`
(274,432 bytes). The backup is retained for rollback of this controlled
upgrade. It is a copy of the known post-incident `0002` state and cannot recover
any hypothetical data that may have existed before the original incident.

## Remaining recovery boundary

No recoverable pre-incident database copy was found. If records had been added
to this development database outside the implemented application paths before
the incident, they may have been lost and cannot be reconstructed from current
workspace evidence.
