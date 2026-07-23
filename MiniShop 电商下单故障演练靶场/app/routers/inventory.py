from fastapi import APIRouter

from app.models import InventoryRequest
from app.services import reserve_inventory

router = APIRouter()


@router.post("/inventory/reserve")
async def reserve(request: InventoryRequest):
    return await reserve_inventory(request)
