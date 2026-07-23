from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "src" / "devops_agent_platform"


def _python_files(path: Path) -> list[Path]:
    return [file for file in path.rglob("*.py") if file.is_file()]


def test_domain_does_not_depend_on_framework_or_orm() -> None:
    forbidden = ("fastapi", "sqlalchemy", "pydantic")

    for file in _python_files(PACKAGE / "domain"):
        content = file.read_text(encoding="utf-8")
        assert all(term not in content for term in forbidden), file


def test_application_does_not_import_infrastructure_implementations() -> None:
    for file in _python_files(PACKAGE / "application"):
        content = file.read_text(encoding="utf-8")
        assert "devops_agent_platform.infrastructure" not in content, file


def test_interfaces_do_not_import_sqlalchemy_models() -> None:
    for file in _python_files(PACKAGE / "interfaces"):
        content = file.read_text(encoding="utf-8")
        assert "sqlalchemy" not in content.lower(), file
