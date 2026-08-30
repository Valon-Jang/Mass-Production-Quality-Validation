"""Runtime schema compatibility contract.

Every new Alembic head must update this constant. The migration regression test
compares it with Alembic's script head so readiness cannot silently drift.
"""

SCHEMA_HEAD_REVISION = "0008"
