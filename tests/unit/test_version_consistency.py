"""Guard against version drift between manifest.json and pyproject.toml.

``manifest.json`` is the authoritative version — it is what Home Assistant
reads at runtime. ``pyproject.toml`` carries a copy only for tooling/audits and
is not used to build or publish (there is no ``[build-system]`` table). The two
silently drifted (manifest at 1.6.2-beta.2 while pyproject sat at 1.2.1 for many
releases); this test makes manifest the single source of truth by failing CI the
moment the copy falls out of sync.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "custom_components" / "aegis_ajax" / "manifest.json"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _manifest_version() -> str:
    return json.loads(_MANIFEST.read_text())["version"]


def _pyproject_version() -> str:
    return tomllib.loads(_PYPROJECT.read_text())["project"]["version"]


def test_pyproject_version_matches_manifest() -> None:
    """pyproject.toml must mirror the authoritative manifest.json version."""
    assert _pyproject_version() == _manifest_version(), (
        "pyproject.toml version is out of sync with manifest.json "
        f"({_pyproject_version()!r} != {_manifest_version()!r}). "
        "manifest.json is the source of truth — update pyproject.toml to match "
        "whenever you bump the release version."
    )
