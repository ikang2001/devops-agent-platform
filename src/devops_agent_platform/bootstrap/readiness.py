from dataclasses import dataclass
from enum import StrEnum


class ComponentReadiness(StrEnum):
    """运行时组件对外暴露的有限就绪状态。"""

    UP = "up"
    DOWN = "down"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReadinessSnapshot:
    """单次就绪检查的不可变结果，避免跨请求共享可变字典。"""

    components: tuple[tuple[str, ComponentReadiness], ...]

    @property
    def ready(self) -> bool:
        """所有启用组件均正常时才允许实例接收业务流量。"""
        return all(
            status in {ComponentReadiness.UP, ComponentReadiness.DISABLED}
            for _, status in self.components
        )

    @property
    def unavailable_components(self) -> tuple[str, ...]:
        """返回未就绪组件名称，不暴露底层连接和异常细节。"""
        return tuple(
            name
            for name, status in self.components
            if status not in {ComponentReadiness.UP, ComponentReadiness.DISABLED}
        )

    def to_dict(self) -> dict[str, object]:
        """转换为稳定的HTTP响应数据。"""
        return {
            "status": "ready" if self.ready else "not_ready",
            "components": {
                name: {"status": status.value}
                for name, status in self.components
            },
        }
