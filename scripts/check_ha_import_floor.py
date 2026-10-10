#!/usr/bin/env python3
"""Check that every Home Assistant import exists in the oldest supported core.

The test suite runs against a recent core only, so an import of something added
later passes CI and fails to load on the minimum we declare in ``hacs.json``.
That is how #574 happened: ``homeassistant.components.web_rtc`` only exists
from 2026.1, and 1.23.1 failed to set up on 2025.11 and 2025.12.

Every ``from homeassistant... import name`` in the integration is looked up in
that core's source on GitHub: the module must exist and define ``name`` (or
have it as a submodule). Imports inside a ``try`` are skipped, since that is
how a newer API is feature-detected.

    python3 scripts/check_ha_import_floor.py             # the declared minimum
    python3 scripts/check_ha_import_floor.py --ha 2026.1.0
"""

from __future__ import annotations

import argparse
import ast
import functools
import json
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INTEGRATION = _REPO_ROOT / "custom_components" / "aegis_ajax"
_HACS = _REPO_ROOT / "hacs.json"

_SOURCE_URL = "https://raw.githubusercontent.com/home-assistant/core/{ref}/{path}"


@functools.cache
def _source(ha: str, module: str) -> str | None:
    base = module.replace(".", "/")
    for path in (f"{base}.py", f"{base}/__init__.py"):
        try:
            with urllib.request.urlopen(_SOURCE_URL.format(ref=ha, path=path), timeout=30) as r:
                return r.read().decode()
        except urllib.error.HTTPError as err:
            if err.code != 404:
                raise
    return None


@functools.cache
def _defined(ha: str, module: str) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(_source(ha, module) or "")):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.TypeAlias):
            names.add(node.name.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return frozenset(names)


def _imports() -> list[tuple[str, str, str]]:
    """(module, name, where) for every unguarded homeassistant import."""
    found = []
    for path in sorted(_INTEGRATION.rglob("*.py")):
        if "proto" in path.relative_to(_INTEGRATION).parts:
            continue
        tree = ast.parse(path.read_text())
        guarded = {id(n) for t in ast.walk(tree) if isinstance(t, ast.Try) for n in ast.walk(t)}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] == "homeassistant"
                and id(node) not in guarded
            ):
                where = f"{path.relative_to(_REPO_ROOT)}:{node.lineno}"
                found += [(node.module, a.name, where) for a in node.names]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ha",
        metavar="VERSION",
        help="Home Assistant release to check against (default: the hacs.json minimum)",
    )
    args = parser.parse_args()

    ha = args.ha or json.loads(_HACS.read_text())["homeassistant"]
    imports = _imports()
    print(f"Home Assistant {ha} vs {len(imports)} imported names")

    failures = []
    for module, name, where in imports:
        if _source(ha, module) is None:
            failures.append(f"{where}: {module} does not exist in Home Assistant {ha}")
        elif name not in _defined(ha, module) and _source(ha, f"{module}.{name}") is None:
            failures.append(f"{where}: {module}.{name} does not exist in Home Assistant {ha}")

    if failures:
        print("\nFAIL (feature-detect it in a try/except ImportError, or raise hacs.json)")
        for failure in dict.fromkeys(failures):
            print(f"  - {failure}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
