"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Enum types ---
    payment_status_enum = postgresql.ENUM(
        "pending", "auto_resolved", "needs_review", "unknown", "completed", "failed",
        name="paymentstatus",
    )
    substack_status_enum = postgresql.ENUM(
        "active", "lapsed", "lifetime",
        name="substackstatus",
    )
    execution_status_enum = postgresql.ENUM(
        "pending", "success", "failed", "manual",
        name="executionstatus",
    )
    payment_status_enum.create(op.get_bind(), checkfirst=True)
    substack_status_enum.create(op.get_bind(), checkfirst=True)
    execution_status_enum.create(op.get_bind(), checkfirst=True)

    # --- payments ---
    op.create_table(
        "payments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("razorpay_payment_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("amount_inr", sa.Integer(), nullable=False),
        sa.Column("payment_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "auto_resolved", "needs_review", "unknown", "completed", "failed",
                name="paymentstatus",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("razorpay_payment_id", name="uq_payments_razorpay_payment_id"),
    )

    # --- subscribers ---
    op.create_table(
        "subscribers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "substack_status",
            sa.Enum("active", "lapsed", "lifetime", name="substackstatus"),
            nullable=False,
        ),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "deleted_from_substack",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.UniqueConstraint("email", name="uq_subscribers_email"),
    )

    # --- actions ---
    op.create_table(
        "actions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id"),
            nullable=False,
        ),
        sa.Column("subscriber_email", sa.String(), nullable=False),
        sa.Column("comp_days", sa.Integer(), nullable=True),
        sa.Column("is_lifetime", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "execution_status",
            sa.Enum("pending", "success", "failed", "manual", name="executionstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # --- clarification_emails ---
    op.create_table(
        "clarification_emails",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id"),
            nullable=False,
        ),
        sa.Column("sent_to_email", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_email", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("clarification_emails")
    op.drop_table("actions")
    op.drop_table("subscribers")
    op.drop_table("payments")
    op.execute("DROP TYPE IF EXISTS executionstatus")
    op.execute("DROP TYPE IF EXISTS substackstatus")
    op.execute("DROP TYPE IF EXISTS paymentstatus")
