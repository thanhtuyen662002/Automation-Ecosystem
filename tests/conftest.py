"""
conftest.py — Ensures test files do not contaminate each other's module singletons.

When test_closed_loop.py reloads core.global_memory via _load(), stealth_brain's
module-level `from core.global_memory import get_global_memory` still holds a
reference to the OLD function object. This hook re-patches that reference so
both modules always use the same live singleton.
"""
import sys


def pytest_runtest_setup(item):
    """Before each test, ensure stealth_brain uses the current global_memory module."""
    sb  = sys.modules.get("core.stealth_brain")
    gm  = sys.modules.get("core.global_memory")
    if sb and gm and hasattr(gm, "get_global_memory"):
        # Re-bind so stealth_brain.evaluate() calls the current module's function
        sb.get_global_memory = gm.get_global_memory

