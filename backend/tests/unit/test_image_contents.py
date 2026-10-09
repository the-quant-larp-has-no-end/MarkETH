"""What runs in the backend image must not need what the image leaves out.

`backend/.dockerignore` keeps `tests/` out of the image: the tests need Docker and run on
the host. `app/`, `scripts/` and `migrations/` are what the api, relay and migrate
containers execute, so none of them may import from `tests`.
"""

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ("app", "scripts", "migrations")


def shipped_modules() -> list[Path]:
    return sorted(path for name in SHIPPED for path in (BACKEND_ROOT / name).rglob("*.py"))


def imported_packages(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            packages.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            packages.add(node.module.partition(".")[0])
    return packages


def test_the_image_excludes_the_tests() -> None:
    ignored = (BACKEND_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "tests/" in ignored


@pytest.mark.parametrize(
    "module", shipped_modules(), ids=lambda path: str(path.relative_to(BACKEND_ROOT))
)
def test_nothing_that_ships_imports_from_the_tests(module: Path) -> None:
    assert "tests" not in imported_packages(module)


def test_every_script_the_makefile_runs_exists() -> None:
    """`make seed` and `make api-client` run `python -m scripts.<name>`."""
    makefile = (BACKEND_ROOT.parent / "Makefile").read_text(encoding="utf-8")
    scripts = {
        word.removeprefix("scripts.") for word in makefile.split() if word.startswith("scripts.")
    }
    assert scripts >= {"seed", "export_openapi"}
    for name in scripts:
        assert (BACKEND_ROOT / "scripts" / f"{name}.py").is_file(), name
