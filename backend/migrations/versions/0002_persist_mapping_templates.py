"""Persist Mapping Template revisions, workflow state, and supersession.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mapping_template_histories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("supplier_scope", sa.String(length=200), nullable=False),
        sa.Column("template_id", sa.String(length=200), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "row_version >= 0",
            name=op.f("ck_mapping_template_histories_mapping_template_history_row_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mapping_template_histories")),
        sa.UniqueConstraint(
            "project_key",
            "supplier_scope",
            "template_id",
            name="uq_mapping_template_history_scope",
        ),
    )
    op.create_table(
        "mapping_template_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("history_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("template_payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("declared_effective_from", sa.Date(), nullable=False),
        sa.Column("declared_effective_to", sa.Date(), nullable=True),
        sa.Column("resolved_effective_to", sa.Date(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=120), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision >= 1",
            name=op.f("ck_mapping_template_revisions_mapping_template_revision_positive"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_mapping_template_revisions_mapping_template_revision_row_version"),
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'REVIEWED', 'APPROVED')",
            name=op.f("ck_mapping_template_revisions_mapping_template_revision_status"),
        ),
        sa.CheckConstraint(
            "declared_effective_to IS NULL OR declared_effective_to >= declared_effective_from",
            name=op.f("ck_mapping_template_revisions_mapping_template_declared_effectivity"),
        ),
        sa.CheckConstraint(
            "resolved_effective_to IS NULL OR resolved_effective_to >= declared_effective_from",
            name=op.f("ck_mapping_template_revisions_mapping_template_resolved_effectivity"),
        ),
        sa.CheckConstraint(
            "(status = 'DRAFT' AND reviewed_by IS NULL AND reviewed_at IS NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'REVIEWED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'APPROVED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name=op.f("ck_mapping_template_revisions_mapping_template_workflow_metadata"),
        ),
        sa.ForeignKeyConstraint(
            ["history_id"],
            ["mapping_template_histories.id"],
            name=op.f("fk_mapping_template_revisions_history_id_mapping_template_histories"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mapping_template_revisions")),
        sa.UniqueConstraint(
            "history_id",
            "revision",
            name="uq_mapping_template_revision_history_revision",
        ),
    )
    op.create_index(
        "ix_mapping_template_revisions_history",
        "mapping_template_revisions",
        ["history_id"],
        unique=False,
    )
    op.create_table(
        "mapping_template_supersessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("history_id", sa.String(length=36), nullable=False),
        sa.Column("predecessor_revision_id", sa.String(length=36), nullable=False),
        sa.Column("successor_revision_id", sa.String(length=36), nullable=False),
        sa.Column("predecessor_effective_to", sa.Date(), nullable=False),
        sa.Column("decided_by", sa.String(length=120), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "predecessor_revision_id <> successor_revision_id",
            name=op.f(
                "ck_mapping_template_supersessions_mapping_template_supersession_distinct_revisions"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["history_id"],
            ["mapping_template_histories.id"],
            name=op.f("fk_mapping_template_supersessions_history_id_mapping_template_histories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_revision_id"],
            ["mapping_template_revisions.id"],
            name=op.f(
                "fk_mapping_template_supersessions_predecessor_revision_id_"
                "mapping_template_revisions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["successor_revision_id"],
            ["mapping_template_revisions.id"],
            name=op.f(
                "fk_mapping_template_supersessions_successor_revision_id_mapping_template_revisions"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mapping_template_supersessions")),
        sa.UniqueConstraint(
            "predecessor_revision_id",
            name="uq_mapping_template_supersession_predecessor",
        ),
        sa.UniqueConstraint(
            "successor_revision_id",
            name="uq_mapping_template_supersession_successor",
        ),
    )
    op.create_index(
        "ix_mapping_template_supersessions_history",
        "mapping_template_supersessions",
        ["history_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mapping_template_supersessions_history",
        table_name="mapping_template_supersessions",
    )
    op.drop_table("mapping_template_supersessions")
    op.drop_index(
        "ix_mapping_template_revisions_history",
        table_name="mapping_template_revisions",
    )
    op.drop_table("mapping_template_revisions")
    op.drop_table("mapping_template_histories")
