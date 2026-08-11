"""Repository-level boundaries that keep the canonical package layout enforceable."""

import importlib
from pathlib import Path

from murmur.canvas.state import register_canvas_tool
from murmur.persistence.repositories.tools import ToolRepo
from murmur.tools.search import register_web_search_tool

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_funcs_package_and_imports_are_absent() -> None:
    assert list((REPOSITORY_ROOT / "funcs").glob("*.py")) == []

    scanned_roots = [REPOSITORY_ROOT / "backend", REPOSITORY_ROOT / "scripts", REPOSITORY_ROOT]
    legacy_imports: list[str] = []
    seen: set[Path] = set()
    for root in scanned_roots:
        candidates = [root / "main.py"] if root == REPOSITORY_ROOT else root.rglob("*.py")
        for path in candidates:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            source = path.read_text(encoding="utf-8")
            if "from funcs" in source or "import funcs" in source:
                legacy_imports.append(str(path.relative_to(REPOSITORY_ROOT)))

    assert legacy_imports == []


def test_builtin_tool_registration_repairs_handler_modules() -> None:
    register_web_search_tool()
    register_canvas_tool()

    expected = {
        "web_search": "murmur.tools.search",
        "canvas_update": "murmur.canvas.state",
    }
    for tool_name, module_name in expected.items():
        tool = ToolRepo.get(tool_name)
        assert tool is not None
        assert tool.handler_module == module_name
        assert callable(getattr(importlib.import_module(module_name), tool.handler_function or ""))
