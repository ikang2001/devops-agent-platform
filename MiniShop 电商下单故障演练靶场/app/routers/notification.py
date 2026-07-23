from fastapi import APIRouter

from app.models import NotificationRequest
from app.services import send_notification

router = APIRouter()


@router.post("/notification/send")
async def send(request: NotificationRequest):
    return await send_notification(request)
