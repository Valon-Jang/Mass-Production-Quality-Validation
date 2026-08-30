# Phase 0 bootstrap and migration regressions

## Hashed clean bootstrap

Symptom -> A brand-new Python 3.12 environment rejected `dev.lock` in
`--require-hashes` mode because `setuptools` was not pinned.

Actual cause -> `pip-tools` treats `pip` and `setuptools` as unsafe packages
unless `--allow-unsafe` is explicit. The already-populated development venv
masked the missing lock entries.

Wrong initial judgment -> Reinstalling the lock into the existing venv was
treated as clean-bootstrap evidence.

Resolution -> Pin `pip==25.0.1` and `setuptools==82.0.1`, compile with
`--allow-unsafe`, validate both hashes, install into a newly created venv, and
then run Bootstrap plus the complete Phase 0 Gate.

Permanent regression test -> `DQ-P0-BOOT-001`, the minimum-count Gate, and
`scripts/Validate-Clean-Lock.ps1`.

## SQLite Alembic revision persistence

Symptom -> SQLite DDL existed but the Alembic revision row could be rolled back
during upgrade/downgrade/re-upgrade testing.

Actual cause -> SQLAlchemy 2 opened an implicit transaction for the SQLite
foreign-key PRAGMA before Alembic opened its migration transaction.

Wrong initial judgment -> Successful DDL creation alone was treated as proof
that the migration revision had committed.

Resolution -> End the PRAGMA transaction before Alembic begins its migration
transaction, and compare the runtime schema revision with the actual migration
head.

Permanent regression test -> `DQ-P0-MIG-001` plus migrated, empty, and stale
readiness tests under `DQ-P0-HEALTH-001`.
