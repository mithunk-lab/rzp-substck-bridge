import uuid
from datetime import date, datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from database import Base


class PaymentStatus(str, PyEnum):
    pending = "pending"
    auto_resolved = "auto_resolved"
    needs_review = "needs_review"
    unknown = "unknown"
    completed = "completed"
    failed = "failed"


class SubstackStatus(str, PyEnum):
    active = "active"
    lapsed = "lapsed"
    lifetime = "lifetime"


class ExecutionStatus(str, PyEnum):
    pending = "pending"
    success = "success"
    failed = "failed"
    manual = "manual"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    razorpay_payment_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    amount_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), nullable=False, default=PaymentStatus.pending
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_match_email: Mapped[str | None] = mapped_column(String, nullable=True)
    suggested_match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    actions: Mapped[list["Action"]] = relationship("Action", back_populates="payment")
    clarification_emails: Mapped[list["ClarificationEmail"]] = relationship(
        "ClarificationEmail", back_populates="payment"
    )


class Subscriber(Base):
    __tablename__ = "subscribers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    substack_status: Mapped[SubstackStatus] = mapped_column(Enum(SubstackStatus), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_from_substack: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=False
    )
    subscriber_email: Mapped[str] = mapped_column(String, nullable=False)
    comp_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_lifetime: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    execution_status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus), nullable=False, default=ExecutionStatus.pending
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    payment: Mapped["Payment"] = relationship("Payment", back_populates="actions")


class ClarificationEmail(Base):
    __tablename__ = "clarification_emails"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=False
    )
    sent_to_email: Mapped[str] = mapped_column(String, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_email: Mapped[str | None] = mapped_column(String, nullable=True)

    payment: Mapped["Payment"] = relationship("Payment", back_populates="clarification_emails")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
