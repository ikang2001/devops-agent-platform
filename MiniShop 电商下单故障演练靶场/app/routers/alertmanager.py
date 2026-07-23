from fastapi import APIRouter, HTTPException, Request, status

from app.agent_alerts import AgentAlertDeliveryError
from app.alertmanager import AlertmanagerRelay, AlertmanagerWebhook


router = APIRouter(prefix="/integrations/alertmanager", tags=["integrations"])


@router.post("/webhook")
async def receive_alertmanager_webhook(
    payload: AlertmanagerWebhook,
    request: Request,
) -> dict[str, object]:
    relay: AlertmanagerRelay | None = getattr(
        request.app.state,
        "alertmanager_relay",
        None,
    )
    if relay is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "ALERT_RELAY_NOT_CONFIGURED",
                "message": "Alertmanager relay is not configured",
            },
        )
    try:
        result = await relay.relay(payload)
    except (AgentAlertDeliveryError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error_code": "ALERT_RELAY_FAILED",
                "message": str(exc),
            },
        ) from exc
    return {
        "received": result.received,
        "relayed": result.relayed,
        "ignored": result.ignored,
        "incident_ids": list(result.incident_ids),
    }
