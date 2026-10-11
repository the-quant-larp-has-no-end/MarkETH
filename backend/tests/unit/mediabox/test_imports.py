"""The box is the standard library and ffmpeg: it imports nothing else, and nothing of the app.

The API in turn does not hold the box: its image leaves `mediabox/` out.
"""

import ast
import sys
from pathlib import Path

import mediabox

PACKAGE = Path(mediabox.__file__).parent
BACKEND = PACKAGE.parent
# What the image of the API runs (backend/Dockerfile, backend/.dockerignore).
SHIPPED_WITH_THE_API = ("app", "scripts", "migrations")
MODULES = sorted(path for path in PACKAGE.glob("*.py"))


def imported(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path.name}: a relative import"
            names.add((node.module or "").split(".")[0])
    return names


def test_the_modules_of_the_box_are_there() -> None:
    assert {path.name for path in MODULES} == {
        "__init__.py",
        "__main__.py",
        "box.py",
        "broadcast.py",
        "ffmpeg.py",
        "playlist.py",
        "probe.py",
        "server.py",
        "settings.py",
        "sniff.py",
    }


def test_no_module_imports_outside_the_standard_library() -> None:
    for path in MODULES:
        foreign = imported(path) - set(sys.stdlib_module_names) - {"mediabox"}
        assert foreign == set(), f"{path.name} imports {sorted(foreign)}"


def test_no_module_imports_from_the_app() -> None:
    for path in MODULES:
        assert "app" not in imported(path), path.name


def test_the_image_of_the_api_leaves_the_box_out() -> None:
    ignored = (BACKEND / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "mediabox/" in ignored


def test_nothing_the_api_runs_imports_the_box() -> None:
    modules = sorted(
        path for name in SHIPPED_WITH_THE_API for path in (BACKEND / name).rglob("*.py")
    )
    assert modules
    for path in modules:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            assert all(name.split(".")[0] != "mediabox" for name in names), path
