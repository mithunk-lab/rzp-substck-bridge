import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel

from models import ExecutionStatus, PaymentStatus, SubstackStatus


class PaymentRead(BaseModel):
    id: uuid.UUID
    razorpay_payment_id: str
    email: str
    name: str
    amount_inr: int
    payment_timestamp: datetime
    status: PaymentStatus
    resolution_notes: Optional[str] = None
    suggested_match_email: Optional[str] = None
    suggested_match_score: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SubscriberRead(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    substack_status: SubstackStatus
    expiry_date: Optional[date] = None
    last_synced_at: datetime
    deleted_from_substack: bool

    model_config = {"from_attributes": True}


class ActionRead(BaseModel):
    id: uuid.UUID
    payment_id: uuid.UUID
    subscriber_email: str
    comp_days: Optional[int] = None
    is_lifetime: bool
    execution_status: ExecutionStatus
    executed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    screenshot_path: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ClarificationEmailRead(BaseModel):
    id: uuid.UUID
    payment_id: uuid.UUID
    sent_to_email: str
    sent_at: datetime
    resolved: bool
    resolved_at: Optional[datetime] = None
    resolved_by_email: Optional[str] = None

    model_config = {"from_attributes": True}
