"""Helpers to test the Home Assistant-free parts of the integration.

``const``, ``model`` and ``api`` do not import Home Assistant, so they can be
loaded (and tested) with plain Python by skipping the package ``__init__``.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import traceback
import types
from typing import Any

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
PKG_DIR = ROOT / "custom_components" / "imou_vacuum"
FIXTURES = TESTS_DIR / "fixtures"


def _ensure_aiohttp() -> None:
    """Plain Python may not have aiohttp; api.py only needs the exception type."""
    try:
        import aiohttp  # noqa: F401, PLC0415
    except ImportError:
        stub = types.ModuleType("aiohttp")
        stub.ClientError = type("ClientError", (Exception,), {})  # type: ignore[attr-defined]
        stub.ClientSession = object  # type: ignore[attr-defined]
        sys.modules["aiohttp"] = stub


def load(name: str) -> Any:
    """Import ``custom_components/imou_vacuum/<name>.py`` without Home Assistant."""
    if "imou_pure" not in sys.modules:
        package = types.ModuleType("imou_pure")
        package.__path__ = [str(PKG_DIR)]  # type: ignore[attr-defined]
        sys.modules["imou_pure"] = package
    if name == "api":
        _ensure_aiohttp()
    return importlib.import_module(f"imou_pure.{name}")


def fixture(name: str) -> Any:
    """Load a JSON test fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def run_all(namespace: dict[str, Any]) -> int:
    """Run every ``test_*`` function of a module; return a process exit code."""
    failed = 0
    tests = [(k, v) for k, v in namespace.items() if k.startswith("test_") and callable(v)]
    for name, func in tests:
        try:
            func()
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
        else:
            print(f"PASS {name}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0
