import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MINISHOP_ROOT = ROOT / "MiniShop 电商下单故障演练靶场"
RUFF_VERSION = "0.15.22"
CRYPTOGRAPHY_SECURITY_FLOOR = (50, 0, 0)


def _read_toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


@pytest.mark.parametrize(
    ("project_root", "project_name"),
    [
        (ROOT, "devops-agent-platform"),
        (MINISHOP_ROOT, "minishop-fault-lab"),
    ],
)
def test_project_lock_contains_project_and_pinned_ruff(
    project_root: Path,
    project_name: str,
) -> None:
    pyproject = _read_toml(project_root / "pyproject.toml")
    lock = _read_toml(project_root / "uv.lock")

    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]
    assert f"ruff=={RUFF_VERSION}" in dev_dependencies
    packages = {
        package["name"]: package["version"]
        for package in lock["package"]
        if "version" in package
    }
    assert project_name in packages
    assert packages[project_name] == pyproject["project"]["version"]
    assert packages["ruff"] == RUFF_VERSION


def test_lock_regeneration_script_pins_the_uv_version() -> None:
    script = (ROOT / "scripts" / "update-lockfiles.ps1").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert '$RequiredUvVersion = "0.11.31"' in script
    assert 'version: "0.11.31"' in workflow
    assert "uv sync --locked --extra dev" in workflow


def test_platform_lock_keeps_cryptography_above_security_floor() -> None:
    lock = _read_toml(ROOT / "uv.lock")
    version = next(
        package["version"]
        for package in lock["package"]
        if package["name"] == "cryptography"
    )
    release = tuple(int(part) for part in version.split("."))

    assert release >= CRYPTOGRAPHY_SECURITY_FLOOR
