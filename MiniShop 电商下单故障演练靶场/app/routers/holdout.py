from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from starlette.responses import JSONResponse

from app.holdout import HOLDOUT_FAULTS, execute_request, holdout_fault_state
from app.models import FaultControlRequest


router = APIRouter()


@router.post("/holdout/faults/reset")
async def reset_holdout_faults() -> dict[str, object]:
    return {"reset": True, "cleared": holdout_fault_state.reset()}


@router.post("/holdout/faults/{fault_name}")
async def enable_holdout_fault(
    fault_name: str,
    request: Optional[FaultControlRequest] = None,
) -> dict[str, object]:
    if fault_name not in HOLDOUT_FAULTS:
        raise HTTPException(status_code=404, detail="unknown holdout fault scenario")
    payload = request or FaultControlRequest()
    spec = holdout_fault_state.enable(fault_name, payload.duration_seconds)
    return {
        "fault": {
            "fault_name": fault_name,
            "service_name": spec.service_name,
            "fault_type": spec.fault_type,
            "expires_in_seconds": payload.duration_seconds,
        }
    }


@router.post("/holdout/{entry}/request")
async def execute_holdout_request(entry: str) -> JSONResponse:
    status_code, body = execute_request(f"/holdout/{entry}/request")
    return JSONResponse(status_code=status_code, content=body)
