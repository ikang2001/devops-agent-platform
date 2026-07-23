from fastapi import APIRouter

from app.models import PaymentRequest
from app.services import pay

router = APIRouter()


@router.post("/payment/pay")
async def pay_order(request: PaymentRequest):
    return await pay(request)
