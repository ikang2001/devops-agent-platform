import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "src" / "devops_agent_platform"


def _python_files(path: Path) -> list[Path]:
    return [file for file in path.rglob("*.py") if file.is_file()]


def _imported_modules(file: Path) -> set[str]:
    """通过 AST 提取真实导入，避免注释和文档字符串造成误报。"""
    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)

    return modules


def _imports_prefix(file: Path, forbidden_prefixes: tuple[str, ...]) -> bool:
    """判断文件是否导入指定模块或其任意子模块。"""
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in _imported_modules(file)
        for prefix in forbidden_prefixes
    )


def test_domain_does_not_depend_on_framework_or_orm() -> None:
    forbidden = ("fastapi", "sqlalchemy", "pydantic")

    for file in _python_files(PACKAGE / "domain"):
        assert not _imports_prefix(file, forbidden), file


def test_application_does_not_import_infrastructure_implementations() -> None:
    forbidden = ("devops_agent_platform.infrastructure",)

    for file in _python_files(PACKAGE / "application"):
        assert not _imports_prefix(file, forbidden), file


def test_interfaces_do_not_import_sqlalchemy_models() -> None:
    for file in _python_files(PACKAGE / "interfaces"):
        assert not _imports_prefix(file, ("sqlalchemy",)), file


def test_ports_do_not_depend_on_sqlalchemy() -> None:
    for file in _python_files(PACKAGE / "ports"):
        assert not _imports_prefix(file, ("sqlalchemy",)), file
