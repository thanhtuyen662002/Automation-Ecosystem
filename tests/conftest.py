"""
conftest.py — Ensures test files do not contaminate each other's module singletons.

When test_closed_loop.py reloads core.global_memory via _load(), stealth_brain's
module-level `from core.global_memory import get_global_memory` still holds a
reference to the OLD function object. This hook re-patches that reference so
both modules always use the same live singleton.
"""
import sys
import types
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE_PATH = _REPO_ROOT / "core"


def _ensure_core_package_stub():
    core_module = sys.modules.get("core")
    if core_module is None:
        core_module = types.ModuleType("core")
        sys.modules["core"] = core_module
    if not hasattr(core_module, "__path__"):
        core_module.__path__ = [str(_CORE_PATH)]
    core_module.__file__ = str(_CORE_PATH / "__init__.py")
    core_module.__package__ = "core"


_ensure_core_package_stub()


def pytest_runtest_setup(item):
    """Before each test, ensure stealth_brain uses the current global_memory module."""
    _ensure_core_package_stub()
    sb  = sys.modules.get("core.stealth_brain")
    gm  = sys.modules.get("core.global_memory")
    if sb and gm and hasattr(gm, "get_global_memory"):
        # Re-bind so stealth_brain.evaluate() calls the current module's function
        sb.get_global_memory = gm.get_global_memory
