from fastapi import APIRouter

from app.models import CheckoutRequest
from app.services import checkout

router = APIRouter()


@router.post("/checkout")
async def create_checkout(request: CheckoutRequest):
    return await checkout(request)
