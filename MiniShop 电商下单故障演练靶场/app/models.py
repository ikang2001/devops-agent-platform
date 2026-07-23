from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class CheckoutItem(BaseModel):
    sku: str = Field(..., min_length=1, max_length=64)
    quantity: int = Field(..., ge=1, le=100)


class CheckoutRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    items: List[CheckoutItem] = Field(..., min_length=1, max_length=20)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class PaymentRequest(BaseModel):
    order_id: str = Field(..., min_length=1, max_length=64)
    amount: float = Field(..., gt=0)
    currency: str = Field(default="CNY", min_length=3, max_length=8)


class InventoryRequest(BaseModel):
    sku: str = Field(..., min_length=1, max_length=64)
    quantity: int = Field(..., ge=1, le=100)


class NotificationRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    order_id: str = Field(..., min_length=1, max_length=64)
    channel: str = Field(default="email", min_length=1, max_length=32)


class FaultControlRequest(BaseModel):
    error_rate: float = Field(default=1.0, ge=0, le=1)
    delay_ms: int = Field(default=1000, ge=0, le=10_000)
    duration_seconds: int = Field(default=300, ge=1, le=3600)
    created_by: str = Field(default="manual", min_length=1, max_length=64)


class FaultRecord(BaseModel):
    fault_id: str
    service_name: str
    fault_type: str
    enabled: bool
    error_rate: float
    delay_ms: int
    starts_at: datetime
    expires_at: datetime
    created_by: str


class ServiceResponse(BaseModel):
    status: str
    trace_id: str
    data: Dict[str, object]
