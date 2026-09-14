"""Keep the declarations that define "supported" from drifting apart.

Four files answer the same question and none of them knew about the others:
``hacs.json`` gates the install, ``pyproject.toml`` floors the dev environment
and the Python version, ``Dockerfile.dev`` picks the interpreter every check
runs on, and ``ci.yml`` picks the interpreters CI exercises. They sat at
"Home Assistant 2024.1.0 / Python 3.12" while the integration could not be
installed below 2025.11.0 and the newest core needs Python 3.14 (#513).

These are coherence guards, not a measurement: what the floor *is* comes from
resolving our requirements against Home Assistant's own constraints file, which
``scripts/check_ha_requirement_floors.py`` does with the network.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HACS = _REPO_ROOT / "hacs.json"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_DOCKERFILE = _REPO_ROOT / "Dockerfile.dev"
_CI = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Home Assistant releases that moved the Python floor, newest first, taken from
# each release's ``requires_python`` metadata on PyPI. Only the releases where
# the floor *changed* are listed; anything in between inherits the entry above
# it. Extend this when a new core raises the requirement.
_HA_PYTHON_FLOORS: tuple[tuple[tuple[int, ...], tuple[int, int]], ...] = (
    ((2026, 3, 0), (3, 14)),  # homeassistant 2026.3.0 declares >=3.14.2
    ((2025, 2, 0), (3, 13)),  # homeassistant 2025.2.0 declares >=3.13
    ((2024, 4, 0), (3, 12)),  # homeassistant 2024.4.0 declares >=3.12
)


def _version(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split("."))


def _hacs_minimum() -> str:
    return json.loads(_HACS.read_text())["homeassistant"]


def _pyproject() -> dict:
    return tomllib.loads(_PYPROJECT.read_text())


def _dev_homeassistant_floor() -> str:
    for dependency in _pyproject()["project"]["optional-dependencies"]["dev"]:
        match = re.fullmatch(r"homeassistant>=(\S+)", dependency.strip())
        if match:
            return match.group(1)
    raise AssertionError("pyproject.toml declares no 'homeassistant>=' dev dependency")


def _requires_python_floor() -> tuple[int, int]:
    raw = _pyproject()["project"]["requires-python"]
    match = re.fullmatch(r">=(\d+)\.(\d+)", raw.strip())
    assert match, f"unexpected requires-python format: {raw!r}"
    return (int(match.group(1)), int(match.group(2)))


def _ci_python_versions() -> list[tuple[int, int]]:
    workflow = yaml.safe_load(_CI.read_text())
    matrix = workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"]
    return [_version(str(entry))[:2] for entry in matrix]  # type: ignore[misc]


def _dockerfile_python() -> tuple[int, int]:
    match = re.search(r"^ARG PYTHON_VERSION=(\S+)$", _DOCKERFILE.read_text(), re.MULTILINE)
    assert match, "Dockerfile.dev declares no default PYTHON_VERSION"
    return _version(match.group(1))[:2]  # type: ignore[return-value]


def _python_floor_for(ha_version: str) -> tuple[int, int]:
    declared = _version(ha_version)
    oldest_known = _HA_PYTHON_FLOORS[-1][0]
    assert declared >= oldest_known, (
        f"declared Home Assistant minimum {ha_version} predates the oldest release in "
        "_HA_PYTHON_FLOORS — re-measure the Python floor from PyPI and extend the table"
    )
    for release, python_floor in _HA_PYTHON_FLOORS:
        if declared >= release:
            return python_floor
    raise AssertionError("unreachable: the table is anchored by the assert above")


def test_hacs_minimum_matches_the_dev_environment_floor() -> None:
    """The version HACS gates on is the version the dev environment installs."""
    assert _hacs_minimum() == _dev_homeassistant_floor(), (
        f"hacs.json gates installs at Home Assistant {_hacs_minimum()} while "
        f"pyproject.toml floors the dev environment at {_dev_homeassistant_floor()}. "
        "Both must name the same release — see CONTRIBUTING, 'Supported versions'."
    )


def test_requires_python_matches_the_declared_home_assistant_minimum() -> None:
    """Our Python floor is whatever the oldest supported core demands."""
    expected = _python_floor_for(_hacs_minimum())
    assert _requires_python_floor() == expected, (
        f"Home Assistant {_hacs_minimum()} requires Python "
        f"{expected[0]}.{expected[1]}, but pyproject.toml declares "
        f">={_requires_python_floor()[0]}.{_requires_python_floor()[1]}"
    )


def test_ruff_target_version_matches_requires_python() -> None:
    """Lint against the oldest interpreter we claim to run on, not a newer one."""
    major, minor = _requires_python_floor()
    assert _pyproject()["tool"]["ruff"]["target-version"] == f"py{major}{minor}"


def test_mypy_target_matches_requires_python() -> None:
    """Type-check against the oldest supported interpreter, like the linter."""
    major, minor = _requires_python_floor()
    assert _pyproject()["tool"]["mypy"]["python_version"] == f"{major}.{minor}"


def test_ci_exercises_every_supported_python() -> None:
    """CI must run on the oldest supported interpreter and on the newest core's.

    A matrix that skips the floor tests an environment no supported install has;
    one that skips the newest tests an environment most installs are already on.
    """
    versions = _ci_python_versions()
    floor = _requires_python_floor()
    newest = max(python for _, python in _HA_PYTHON_FLOORS)
    assert floor in versions, f"CI does not run on Python {floor[0]}.{floor[1]}, our floor"
    assert newest in versions, (
        f"CI does not run on Python {newest[0]}.{newest[1]}, which the newest "
        "Home Assistant core requires"
    )


def test_ci_never_runs_an_unsupported_python() -> None:
    """Every leg of the matrix must be an interpreter a supported core runs on."""
    floor = _requires_python_floor()
    for version in _ci_python_versions():
        assert version >= floor, (
            f"CI runs Python {version[0]}.{version[1]}, below our declared floor "
            f"{floor[0]}.{floor[1]} — no supported Home Assistant install uses it"
        )


def test_dev_container_default_python_is_supported() -> None:
    """`make check` must not run on an interpreter we do not support."""
    assert _dockerfile_python() >= _requires_python_floor()
