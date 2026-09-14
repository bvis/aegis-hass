#!/usr/bin/env python3
"""Check our requirement floors against Home Assistant's own pins.

Home Assistant does not pip-install an integration's requirements freely: it
passes its own ``homeassistant/package_constraints.txt`` as a constraint file,
and that file pins several of the packages we depend on *exactly*. A floor of
ours that sits above the version the oldest supported core pins cannot be
resolved at all — the install ends in ``ResolutionImpossible`` and the user
sees "Requirements for aegis_ajax not found", with nothing in our code to
blame. That is how #513 happened: ``grpcio>=1.75.1`` against a core pinning
``grpcio==1.72.1``.

Two checks, because they fail differently:

* **pins** — compare each ``name>=floor`` in ``manifest.json`` against the
  constraint line for that package. Deterministic, and the error names the
  exact package and both versions.
* **resolution** — ``pip install --dry-run --ignore-installed`` of every
  requirement under the constraint file. Slower and needs the index, but it is
  the only thing that sees a *transitive* collision (a dependency of one of our
  requirements capped below what the core pins).

``--ignore-installed`` is not optional: without it pip reports anything already
present in the environment as satisfied and never consults the constraints, so
the check passes in the dev image no matter what.

    python3 scripts/check_ha_requirement_floors.py             # the declared minimum
    python3 scripts/check_ha_requirement_floors.py --ha 2026.9.2
    python3 scripts/check_ha_requirement_floors.py --pins-only # no index access
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST = _REPO_ROOT / "custom_components" / "aegis_ajax" / "manifest.json"
_HACS = _REPO_ROOT / "hacs.json"

_CONSTRAINTS_URL = (
    "https://raw.githubusercontent.com/home-assistant/core/{ref}"
    "/homeassistant/package_constraints.txt"
)
_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)\s*(?P<operator>[<>=!~]=)\s*(?P<version>\S+)$"
)


def _version(raw: str) -> tuple[int, ...]:
    """Parse a release string into a comparable tuple, ignoring any suffix."""
    parts = []
    for chunk in raw.split("."):
        match = re.match(r"\d+", chunk)
        if not match:
            break
        parts.append(int(match.group()))
    return tuple(parts)


def _declared_minimum() -> str:
    return json.loads(_HACS.read_text())["homeassistant"]


def _requirements() -> list[str]:
    return list(json.loads(_MANIFEST.read_text())["requirements"])


def _fetch_constraints(ref: str) -> str:
    url = _CONSTRAINTS_URL.format(ref=ref)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed https URL
            return response.read().decode()
    except urllib.error.HTTPError as err:
        raise SystemExit(
            f"no package_constraints.txt at home-assistant/core tag {ref} ({err.code}). "
            "hacs.json must name a real Home Assistant release."
        ) from err


def _parse_constraints(text: str) -> dict[str, tuple[str, str]]:
    """Map package name -> (operator, version) for every pinned constraint."""
    pins: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        match = _REQUIREMENT.fullmatch(stripped)
        if match:
            pins[match["name"].lower()] = (match["operator"], match["version"])
    return pins


def _check_pins(requirements: list[str], pins: dict[str, tuple[str, str]], ha: str) -> list[str]:
    """Report every floor of ours that the core's pins cannot satisfy."""
    failures = []
    for requirement in requirements:
        match = _REQUIREMENT.fullmatch(requirement.strip())
        if not match or match["operator"] != ">=":
            print(f"  ?  {requirement} — not a '>=' floor, only the resolution check covers it")
            continue
        name, floor = match["name"].lower(), match["version"]
        pin = pins.get(name)
        if pin is None:
            print(f"  ok {requirement} — Home Assistant {ha} does not constrain {name}")
            continue
        operator, pinned = pin
        if operator == ">=" or _version(pinned) >= _version(floor):
            print(f"  ok {requirement} — Home Assistant {ha} has {name}{operator}{pinned}")
            continue
        failures.append(
            f"{requirement} cannot be satisfied: Home Assistant {ha} pins "
            f"{name}{operator}{pinned}. "
            f"Lower our floor, or raise the minimum in hacs.json to a core pinning {name}>={floor}."
        )
    return failures


def _check_resolution(requirements: list[str], constraints: str, ha: str) -> list[str]:
    """Ask pip to resolve our requirements under the core's constraint file."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write(constraints)
        constraint_path = handle.name
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--quiet",
        "--no-input",
        "--constraint",
        constraint_path,
        *requirements,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if completed.returncode == 0:
        print(f"  ok pip resolves every requirement under the Home Assistant {ha} constraints")
        return []
    detail = (completed.stderr or completed.stdout).strip().splitlines()
    return [
        f"pip cannot resolve our requirements under the Home Assistant {ha} constraints:\n    "
        + "\n    ".join(detail[-6:])
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ha",
        metavar="VERSION",
        help="Home Assistant release to check against (default: the hacs.json minimum)",
    )
    parser.add_argument(
        "--pins-only",
        action="store_true",
        help="skip the pip resolution check (no package index access)",
    )
    args = parser.parse_args()

    ha = args.ha or _declared_minimum()
    requirements = _requirements()
    print(f"Home Assistant {ha} vs {len(requirements)} requirements from manifest.json")

    pins = _parse_constraints(_fetch_constraints(ha))
    failures = _check_pins(requirements, pins, ha)
    if not args.pins_only:
        failures += _check_resolution(requirements, _fetch_constraints(ha), ha)

    if failures:
        print("\nFAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
