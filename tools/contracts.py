"""Load the THEME-1 reference validator from a leaf-contracts checkout.

There is no vendored copy of the validator in this repository. CI checks out
leaf-contracts at a pinned commit and points LEAF_CONTRACTS_DIR at it; local
development and the tests use a sibling checkout (../leaf-contracts).
"""
from __future__ import annotations

import importlib
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def contracts_dir() -> str:
    configured = os.environ.get("LEAF_CONTRACTS_DIR")
    if configured:
        return os.path.abspath(configured)
    return os.path.abspath(os.path.join(REPO_ROOT, "..", "leaf-contracts"))


def theme_model():
    """Import and return leaf-contracts' theme_model module."""
    base = contracts_dir()
    scripts = os.path.join(base, "contracts", "leaf-themes", "scripts")
    services = os.path.join(base, "contracts", "leaf-services", "scripts")
    if not os.path.isfile(os.path.join(scripts, "theme_model.py")):
        raise RuntimeError(
            f"theme_model.py not found under {scripts}; set LEAF_CONTRACTS_DIR "
            "to a leaf-contracts checkout")
    sys.dont_write_bytecode = True
    for path in (services, scripts):
        if path not in sys.path:
            sys.path.insert(0, path)
    return importlib.import_module("theme_model")
