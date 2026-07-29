"""DevOps 智能排障 Agent 平台主包。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("devops-agent-platform")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = ["__version__"]
