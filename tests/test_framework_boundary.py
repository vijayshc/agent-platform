"""The framework must not depend on sample content or setup scripts.

This is the guarantee behind "delete ``platform_samples/`` and the app still
boots": no first-party module may import samples, the setup/feed scripts, or the
retired seed package. Prose that *points* at those scripts (log hints like "run
scripts/setup_platform.py") is fine; imports are not.

The scan covers ``src/`` and ``app.py`` (not tests/scripts, which legitimately
import sample code) and resolves relative as well as absolute and dynamic
imports, so ``from .seeds import x`` or
``importlib.import_module("platform_samples.feed")`` fail the same way.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCAN_ROOTS = (ROOT / "src", ROOT / "app.py")

#: Import roots no first-party module may depend on.
FORBIDDEN_ROOTS = ("platform_samples", "scripts", "seeds")
#: Module prefixes that only exist in the retired sample package.
FORBIDDEN_PREFIXES = ("src.agent_platform.seeds",)

_DYNAMIC_IMPORTERS = ("import_module", "__import__", "spec_from_file_location")


def _module_is_forbidden(module: str) -> bool:
    if not module:
        return False
    if module.startswith(FORBIDDEN_PREFIXES):
        return True
    head = module.split(".")[0]
    return head in FORBIDDEN_ROOTS


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Absolute, relative, and literal dynamic imports in one file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            # level > 0 means relative: "from .seeds import x" / "from ..seeds import x"
            modules.update(
                alias.name if node.level and not base else f"{base}.{alias.name}"
                for alias in node.names
            )
            if node.level and base:
                modules.add(base)
        elif isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name not in _DYNAMIC_IMPORTERS:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    modules.add(arg.value)
    return modules


def _scanned_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root in SCAN_ROOTS:
        files.extend(sorted(root.rglob("*.py")) if root.is_dir() else [root])
    return files


def test_framework_never_imports_sample_or_setup_code() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _scanned_files():
        bad = sorted(m for m in _imported_modules(path) if _module_is_forbidden(m))
        if bad:
            offenders[str(path.relative_to(ROOT))] = bad
    assert not offenders, f"first-party code imports sample/setup/seed code: {offenders}"


def test_guard_catches_relative_and_dynamic_imports(tmp_path) -> None:
    """The guard must fail on the shapes an absolute-import scan would miss."""
    cases = {
        "relative": "from .seeds import helper\n",
        "relative_up": "from ..seeds.mcp import seed\n",
        "dynamic": 'import importlib\nimportlib.import_module("platform_samples.feed")\n',
        "dunder": '__import__("scripts.feed_samples")\n',
        "absolute_seeds": "from src.agent_platform.seeds import mcp\n",
        "clean": "from src.agent_platform.catalog.store import DefinitionStore\n",
    }
    results = {}
    for name, source in cases.items():
        candidate = tmp_path / f"{name}.py"
        candidate.write_text(source, encoding="utf-8")
        results[name] = sorted(
            m for m in _imported_modules(candidate) if _module_is_forbidden(m)
        )
    assert results["relative"], results
    assert results["relative_up"], results
    assert results["dynamic"], results
    assert results["dunder"], results
    assert results["absolute_seeds"], results
    assert not results["clean"], results


def test_sample_mcp_servers_are_importable_and_expose_fastmcp() -> None:
    """The bundled servers stay launchable (stdio subprocess or HTTP service)."""
    from mcp.server.fastmcp import FastMCP

    from platform_samples.mcp_servers import knowledge, text2sql, workspace

    for module in (workspace, text2sql, knowledge):
        assert isinstance(getattr(module, "mcp", None), FastMCP), module.__name__


def test_workspace_fixture_seeding_survives_missing_samples(monkeypatch, tmp_path) -> None:
    """A workspace seed that names a sample fixture must no-op, not explode."""
    from src.agent_platform.runtime import workspace as workspace_mod

    monkeypatch.setitem(
        workspace_mod.WORKSPACE_SEEDS, "sample-service", tmp_path / "not-installed"
    )
    destination = workspace_mod.seed_workspace(tmp_path / "run", "sample-service")
    assert destination is None


def test_bootstrap_documents_the_setup_script() -> None:
    from src.agent_platform.bootstrap import CORE_TABLES, SETUP_HINT

    assert "setup_platform.py" in SETUP_HINT
    assert {"users", "agent_definitions", "mcp_servers"} <= set(CORE_TABLES)
