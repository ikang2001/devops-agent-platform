import asyncio
from collections.abc import Mapping

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.ticketing import (
    TicketingGatewayPort,
    TicketingSubmitOutcome,
    TicketingSubmitRequest,
)


class RoutingTicketingGateway:
    """按规范化 target_system 选择供应商适配器。"""

    def __init__(
        self,
        routes: Mapping[str, TicketingGatewayPort],
        fallback: TicketingGatewayPort | None = None,
    ) -> None:
        normalized: dict[str, TicketingGatewayPort] = {}
        for target, gateway in routes.items():
            if (
                not isinstance(target, str)
                or target != target.strip().lower()
                or not target
                or len(target) > 128
                or any(
                    not (character.isalnum() or character in "._-")
                    for character in target
                )
            ):
                raise AppValidationError("ticketing route target is invalid")
            if target in normalized:
                raise AppValidationError("ticketing route target is duplicated")
            normalized[target] = gateway
        if not normalized and fallback is None:
            raise AppValidationError("ticketing router requires at least one gateway")
        self._routes = normalized
        self._fallback = fallback
        self._closed = False

    async def submit_ticket(
        self,
        request: TicketingSubmitRequest,
    ) -> TicketingSubmitOutcome:
        if not isinstance(request, TicketingSubmitRequest):
            raise AppValidationError("request must be a TicketingSubmitRequest")
        if self._closed:
            return TicketingSubmitOutcome(
                succeeded=False,
                failure_reason="Ticketing router is closed",
            )
        gateway = self._routes.get(request.target_system.lower())
        gateway = gateway or self._fallback
        if gateway is None:
            return TicketingSubmitOutcome(
                succeeded=False,
                failure_reason="Unsupported ticket target system",
            )
        return await gateway.submit_ticket(request)

    async def close(self) -> None:
        """关闭路由拥有的唯一子网关，重复引用不会重复关闭。"""
        if self._closed:
            return
        self._closed = True
        gateways = [*self._routes.values()]
        if self._fallback is not None:
            gateways.append(self._fallback)
        seen: set[int] = set()
        errors: list[Exception] = []
        for gateway in gateways:
            identity = id(gateway)
            if identity in seen:
                continue
            seen.add(identity)
            close = getattr(gateway, "close", None)
            if close is None:
                continue
            try:
                await close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup(
                "Failed to close ticketing gateways",
                errors,
            )
