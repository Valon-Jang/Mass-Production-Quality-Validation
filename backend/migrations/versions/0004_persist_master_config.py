"""Persist canonical hierarchy, numeric Master Specs, and row bindings.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_mapping_template_history_scope_id",
        "mapping_template_histories",
        ["project_key", "supplier_scope", "template_id", "id"],
        unique=True,
    )
    op.create_index(
        "uq_mapping_template_revision_history_revision_id",
        "mapping_template_revisions",
        ["history_id", "revision", "id"],
        unique=True,
    )
    op.create_table(
        "canonical_models",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("model_key", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_canonical_models_canonical_model_row_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_models")),
        sa.UniqueConstraint(
            "project_key",
            "model_key",
            name="uq_canonical_model_key",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            name="uq_canonical_model_project_id",
        ),
    )
    op.create_table(
        "canonical_suppliers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("supplier_key", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_canonical_suppliers_canonical_supplier_row_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_suppliers")),
        sa.UniqueConstraint(
            "project_key",
            "supplier_key",
            name="uq_canonical_supplier_key",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            name="uq_canonical_supplier_project_id",
        ),
    )
    op.create_table(
        "canonical_model_parts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("model_id", sa.String(length=36), nullable=False),
        sa.Column("model_part_key", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_canonical_model_parts_canonical_model_part_row_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "model_id"],
            ["canonical_models.project_key", "canonical_models.id"],
            name="fk_canonical_model_part_project_model",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_model_parts")),
        sa.UniqueConstraint(
            "project_key",
            "model_part_key",
            name="uq_canonical_model_part_key",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            name="uq_canonical_model_part_project_id",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "model_id",
            name="uq_canonical_model_part_project_id_model",
        ),
    )
    op.create_index(
        "ix_canonical_model_parts_model",
        "canonical_model_parts",
        ["project_key", "model_id"],
        unique=False,
    )
    op.create_table(
        "canonical_inspection_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("model_part_id", sa.String(length=36), nullable=False),
        sa.Column("item_key", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("disposition", sa.String(length=24), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "disposition IN ('CANDIDATE', 'MANAGED', 'EXCLUDED')",
            name=op.f("ck_canonical_inspection_items_canonical_inspection_item_disposition"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_canonical_inspection_items_canonical_inspection_item_row_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "model_part_id"],
            ["canonical_model_parts.project_key", "canonical_model_parts.id"],
            name="fk_canonical_inspection_item_project_part",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_inspection_items")),
        sa.UniqueConstraint(
            "project_key",
            "item_key",
            name="uq_canonical_inspection_item_key",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            name="uq_canonical_inspection_item_project_id",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "model_part_id",
            name="uq_canonical_inspection_item_project_id_part",
        ),
    )
    op.create_index(
        "ix_canonical_inspection_items_part",
        "canonical_inspection_items",
        ["project_key", "model_part_id"],
        unique=False,
    )
    op.create_table(
        "master_spec_histories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "row_version >= 0",
            name=op.f("ck_master_spec_histories_master_spec_history_row_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "item_id"],
            ["canonical_inspection_items.project_key", "canonical_inspection_items.id"],
            name="fk_master_spec_history_project_item",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_spec_histories")),
        sa.UniqueConstraint(
            "project_key",
            "item_id",
            name="uq_master_spec_history_item",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            name="uq_master_spec_history_project_id",
        ),
    )
    op.create_table(
        "master_spec_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("history_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("spec_payload", sa.JSON(), nullable=False),
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
            name=op.f("ck_master_spec_revisions_master_spec_revision_positive"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_master_spec_revisions_master_spec_revision_row_version"),
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64",
            name=op.f("ck_master_spec_revisions_master_spec_payload_digest_length"),
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'REVIEWED', 'APPROVED')",
            name=op.f("ck_master_spec_revisions_master_spec_revision_status"),
        ),
        sa.CheckConstraint(
            "declared_effective_to IS NULL OR declared_effective_to >= declared_effective_from",
            name=op.f("ck_master_spec_revisions_master_spec_declared_effectivity"),
        ),
        sa.CheckConstraint(
            "resolved_effective_to IS NULL OR resolved_effective_to >= declared_effective_from",
            name=op.f("ck_master_spec_revisions_master_spec_resolved_effectivity"),
        ),
        sa.CheckConstraint(
            "resolved_effective_to IS NULL OR declared_effective_to IS NULL "
            "OR resolved_effective_to <= declared_effective_to",
            name=op.f("ck_master_spec_revisions_master_spec_resolved_does_not_extend_declared"),
        ),
        sa.CheckConstraint(
            "(status = 'DRAFT' AND reviewed_by IS NULL AND reviewed_at IS NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'REVIEWED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'APPROVED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name=op.f("ck_master_spec_revisions_master_spec_workflow_metadata"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "history_id"],
            ["master_spec_histories.project_key", "master_spec_histories.id"],
            name="fk_master_spec_revision_project_history",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_spec_revisions")),
        sa.UniqueConstraint(
            "history_id",
            "revision",
            name="uq_master_spec_history_revision",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "history_id",
            name="uq_master_spec_revision_project_id_history",
        ),
    )
    op.create_index(
        "ix_master_spec_revisions_history",
        "master_spec_revisions",
        ["project_key", "history_id"],
        unique=False,
    )
    op.create_index(
        "ix_master_spec_revisions_effective",
        "master_spec_revisions",
        ["project_key", "status", "declared_effective_from"],
        unique=False,
    )
    op.create_table(
        "master_spec_supersessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("history_id", sa.String(length=36), nullable=False),
        sa.Column("predecessor_revision_id", sa.String(length=36), nullable=False),
        sa.Column("successor_revision_id", sa.String(length=36), nullable=False),
        sa.Column("predecessor_effective_to", sa.Date(), nullable=False),
        sa.Column("decided_by", sa.String(length=120), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "predecessor_revision_id <> successor_revision_id",
            name=op.f("ck_master_spec_supersessions_master_spec_supersession_distinct"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "history_id"],
            ["master_spec_histories.project_key", "master_spec_histories.id"],
            name="fk_master_spec_supersession_project_history",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "predecessor_revision_id", "history_id"],
            [
                "master_spec_revisions.project_key",
                "master_spec_revisions.id",
                "master_spec_revisions.history_id",
            ],
            name="fk_master_spec_supersession_predecessor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "successor_revision_id", "history_id"],
            [
                "master_spec_revisions.project_key",
                "master_spec_revisions.id",
                "master_spec_revisions.history_id",
            ],
            name="fk_master_spec_supersession_successor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_spec_supersessions")),
        sa.UniqueConstraint(
            "predecessor_revision_id",
            name="uq_master_spec_supersession_predecessor",
        ),
        sa.UniqueConstraint(
            "successor_revision_id",
            name="uq_master_spec_supersession_successor",
        ),
    )
    op.create_index(
        "ix_master_spec_supersessions_history",
        "master_spec_supersessions",
        ["project_key", "history_id"],
        unique=False,
    )
    op.create_table(
        "canonical_row_binding_histories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("supplier_scope", sa.String(length=200), nullable=False),
        sa.Column("template_id", sa.String(length=200), nullable=False),
        sa.Column("template_revision", sa.Integer(), nullable=False),
        sa.Column("row_key", sa.String(length=200), nullable=False),
        sa.Column("canonical_supplier_id", sa.String(length=36), nullable=False),
        sa.Column("mapping_history_id", sa.String(length=36), nullable=False),
        sa.Column("mapping_revision_id", sa.String(length=36), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "template_revision >= 1",
            name=op.f("ck_canonical_row_binding_histories_canonical_row_binding_template_revision"),
        ),
        sa.CheckConstraint(
            "row_version >= 0",
            name=op.f(
                "ck_canonical_row_binding_histories_canonical_row_binding_history_row_version"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "canonical_supplier_id"],
            ["canonical_suppliers.project_key", "canonical_suppliers.id"],
            name="fk_canonical_row_binding_history_supplier",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "supplier_scope", "template_id", "mapping_history_id"],
            [
                "mapping_template_histories.project_key",
                "mapping_template_histories.supplier_scope",
                "mapping_template_histories.template_id",
                "mapping_template_histories.id",
            ],
            name="fk_canonical_row_binding_history_mapping_history",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["mapping_history_id", "template_revision", "mapping_revision_id"],
            [
                "mapping_template_revisions.history_id",
                "mapping_template_revisions.revision",
                "mapping_template_revisions.id",
            ],
            name="fk_canonical_row_binding_history_mapping_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_row_binding_histories")),
        sa.UniqueConstraint(
            "project_key",
            "supplier_scope",
            "template_id",
            "template_revision",
            "row_key",
            name="uq_canonical_row_binding_history_key",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            name="uq_canonical_row_binding_history_project_id",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "canonical_supplier_id",
            name="uq_canonical_row_binding_history_project_id_supplier",
        ),
    )
    op.create_index(
        "ix_canonical_row_binding_histories_mapping",
        "canonical_row_binding_histories",
        ["mapping_history_id", "mapping_revision_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_row_binding_histories_supplier",
        "canonical_row_binding_histories",
        ["project_key", "canonical_supplier_id"],
        unique=False,
    )
    op.create_table(
        "canonical_row_binding_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("history_id", sa.String(length=36), nullable=False),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("binding_payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("canonical_model_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_supplier_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_model_part_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_item_id", sa.String(length=36), nullable=False),
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
            "binding_revision >= 1",
            name=op.f("ck_canonical_row_binding_revisions_canonical_row_binding_revision_positive"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f(
                "ck_canonical_row_binding_revisions_canonical_row_binding_revision_row_version"
            ),
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64",
            name=op.f(
                "ck_canonical_row_binding_revisions_canonical_row_binding_payload_digest_length"
            ),
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'REVIEWED', 'APPROVED')",
            name=op.f("ck_canonical_row_binding_revisions_canonical_row_binding_revision_status"),
        ),
        sa.CheckConstraint(
            "declared_effective_to IS NULL OR declared_effective_to >= declared_effective_from",
            name=op.f(
                "ck_canonical_row_binding_revisions_canonical_row_binding_declared_effectivity"
            ),
        ),
        sa.CheckConstraint(
            "resolved_effective_to IS NULL OR resolved_effective_to >= declared_effective_from",
            name=op.f(
                "ck_canonical_row_binding_revisions_canonical_row_binding_resolved_effectivity"
            ),
        ),
        sa.CheckConstraint(
            "resolved_effective_to IS NULL OR declared_effective_to IS NULL "
            "OR resolved_effective_to <= declared_effective_to",
            name=op.f(
                "ck_canonical_row_binding_revisions_"
                "canonical_row_binding_resolved_does_not_extend_declared"
            ),
        ),
        sa.CheckConstraint(
            "(status = 'DRAFT' AND reviewed_by IS NULL AND reviewed_at IS NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'REVIEWED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'APPROVED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name=op.f("ck_canonical_row_binding_revisions_canonical_row_binding_workflow_metadata"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "history_id", "canonical_supplier_id"],
            [
                "canonical_row_binding_histories.project_key",
                "canonical_row_binding_histories.id",
                "canonical_row_binding_histories.canonical_supplier_id",
            ],
            name="fk_canonical_row_binding_revision_history",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "canonical_model_id"],
            ["canonical_models.project_key", "canonical_models.id"],
            name="fk_canonical_row_binding_revision_model",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "canonical_supplier_id"],
            ["canonical_suppliers.project_key", "canonical_suppliers.id"],
            name="fk_canonical_row_binding_revision_supplier",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "canonical_model_part_id", "canonical_model_id"],
            [
                "canonical_model_parts.project_key",
                "canonical_model_parts.id",
                "canonical_model_parts.model_id",
            ],
            name="fk_canonical_row_binding_revision_model_part",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "canonical_item_id", "canonical_model_part_id"],
            [
                "canonical_inspection_items.project_key",
                "canonical_inspection_items.id",
                "canonical_inspection_items.model_part_id",
            ],
            name="fk_canonical_row_binding_revision_item",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_row_binding_revisions")),
        sa.UniqueConstraint(
            "history_id",
            "binding_revision",
            name="uq_canonical_row_binding_history_revision",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "history_id",
            name="uq_canonical_row_binding_revision_project_id_history",
        ),
    )
    op.create_index(
        "ix_canonical_row_binding_revisions_history",
        "canonical_row_binding_revisions",
        ["project_key", "history_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_row_binding_revisions_effective",
        "canonical_row_binding_revisions",
        ["project_key", "status", "declared_effective_from"],
        unique=False,
    )
    op.create_table(
        "canonical_row_binding_supersessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
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
                "ck_canonical_row_binding_supersessions_canonical_row_binding_supersession_distinct"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "history_id"],
            [
                "canonical_row_binding_histories.project_key",
                "canonical_row_binding_histories.id",
            ],
            name="fk_canonical_row_binding_supersession_history",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "predecessor_revision_id", "history_id"],
            [
                "canonical_row_binding_revisions.project_key",
                "canonical_row_binding_revisions.id",
                "canonical_row_binding_revisions.history_id",
            ],
            name="fk_canonical_row_binding_supersession_predecessor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "successor_revision_id", "history_id"],
            [
                "canonical_row_binding_revisions.project_key",
                "canonical_row_binding_revisions.id",
                "canonical_row_binding_revisions.history_id",
            ],
            name="fk_canonical_row_binding_supersession_successor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_canonical_row_binding_supersessions"),
        ),
        sa.UniqueConstraint(
            "predecessor_revision_id",
            name="uq_canonical_row_binding_supersession_predecessor",
        ),
        sa.UniqueConstraint(
            "successor_revision_id",
            name="uq_canonical_row_binding_supersession_successor",
        ),
    )
    op.create_index(
        "ix_canonical_row_binding_supersessions_history",
        "canonical_row_binding_supersessions",
        ["project_key", "history_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_row_binding_supersessions_history",
        table_name="canonical_row_binding_supersessions",
    )
    op.drop_table("canonical_row_binding_supersessions")
    op.drop_index(
        "ix_canonical_row_binding_revisions_effective",
        table_name="canonical_row_binding_revisions",
    )
    op.drop_index(
        "ix_canonical_row_binding_revisions_history",
        table_name="canonical_row_binding_revisions",
    )
    op.drop_table("canonical_row_binding_revisions")
    op.drop_index(
        "ix_canonical_row_binding_histories_supplier",
        table_name="canonical_row_binding_histories",
    )
    op.drop_index(
        "ix_canonical_row_binding_histories_mapping",
        table_name="canonical_row_binding_histories",
    )
    op.drop_table("canonical_row_binding_histories")
    op.drop_index(
        "ix_master_spec_supersessions_history",
        table_name="master_spec_supersessions",
    )
    op.drop_table("master_spec_supersessions")
    op.drop_index(
        "ix_master_spec_revisions_effective",
        table_name="master_spec_revisions",
    )
    op.drop_index(
        "ix_master_spec_revisions_history",
        table_name="master_spec_revisions",
    )
    op.drop_table("master_spec_revisions")
    op.drop_table("master_spec_histories")
    op.drop_index(
        "ix_canonical_inspection_items_part",
        table_name="canonical_inspection_items",
    )
    op.drop_table("canonical_inspection_items")
    op.drop_index(
        "ix_canonical_model_parts_model",
        table_name="canonical_model_parts",
    )
    op.drop_table("canonical_model_parts")
    op.drop_table("canonical_suppliers")
    op.drop_table("canonical_models")
    op.drop_index(
        "uq_mapping_template_revision_history_revision_id",
        table_name="mapping_template_revisions",
    )
    op.drop_index(
        "uq_mapping_template_history_scope_id",
        table_name="mapping_template_histories",
    )
