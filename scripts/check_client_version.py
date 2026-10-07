"""Warn before Ajax deprecates the app version the integration reports.

The Ajax app reads a public file listing, per app and platform, the version
at or below which it nags the user to update (`hourlyReminderVersion`) and
the one at or below which it blocks until updated (`criticalReminderVersion`).
This compares our `CLIENT_VERSION` against the main Android app's thresholds.

Exit 0 = fine, 1 = our version is at or below a threshold (message on stdout),
2 = the file could not be read or parsed.

Note: Ajax blacklisted "3.30" (#559) without listing it here, so a clean run
is not a guarantee, only the absence of a formal deprecation.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

URL = "https://app.prod.ajax.systems/utilities/deprecation/versions.json"
CONST = Path(__file__).resolve().parent.parent / "custom_components/aegis_ajax/const.py"


def _parts(version: str) -> list[int]:
    return [int(p) for p in version.split(".")]


def is_newer(version: str, threshold: str) -> bool:
    """Same rule as the app: missing components count as 0."""
    a, b = _parts(version), _parts(threshold)
    width = max(len(a), len(b))
    return a + [0] * (width - len(a)) > b + [0] * (width - len(b))


def check(client_version: str, data: dict) -> list[str]:
    """Return one line per threshold our version fails, worst first."""
    if not data.get("enabled"):
        return []
    problems: list[str] = []
    for label, entry in data["main"]["android"].items():
        for key, level in (
            ("criticalReminderVersion", "CRITICAL"),
            ("hourlyReminderVersion", "WARNING"),
        ):
            threshold = (entry.get(key) or "").strip()
            if threshold and not is_newer(client_version, threshold):
                problems.append(
                    f"{level}: CLIENT_VERSION {client_version} is at or below "
                    f"{key} {threshold} (label {label})"
                )
    return sorted(problems)


def main() -> int:
    match = re.search(r'^CLIENT_VERSION = "([^"]+)"', CONST.read_text(), re.MULTILINE)
    if not match:
        print("CLIENT_VERSION not found in const.py")
        return 2
    try:
        with urllib.request.urlopen(URL, timeout=30) as resp:  # noqa: S310 - fixed https URL
            data = json.load(resp)
        problems = check(match.group(1), data)
    except Exception as err:  # noqa: BLE001
        print(f"Could not read {URL}: {err}")
        return 2
    if not problems:
        print(f"CLIENT_VERSION {match.group(1)} is above every threshold")
        return 0
    print("\n".join(problems))
    return 1


if __name__ == "__main__":
    sys.exit(main())
