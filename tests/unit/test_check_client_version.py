"""Tests for scripts/check_client_version.py (the daily CI watch)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_client_version",
    Path(__file__).resolve().parents[2] / "scripts/check_client_version.py",
)
assert _SPEC and _SPEC.loader
ccv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ccv)


def _data(hourly: str, critical: str, *, enabled: bool = True) -> dict:
    entry = {"hourlyReminderVersion": hourly, "criticalReminderVersion": critical, "exclude": []}
    return {"enabled": enabled, "main": {"android": {"default": entry}}}


def test_is_newer_pads_missing_components() -> None:
    assert ccv.is_newer("3.57", "3.21")
    assert not ccv.is_newer("3.21", "3.21.0")
    assert not ccv.is_newer("3.30", "3.30")
    assert ccv.is_newer("3.30.1", "3.30")


def test_check_reports_each_threshold_worst_first() -> None:
    assert ccv.check("3.57", _data("3.21", "")) == []
    problems = ccv.check("3.30", _data("3.40", "3.30"))
    assert [p.split(":")[0] for p in problems] == ["CRITICAL", "WARNING"]


def test_check_ignores_disabled_file() -> None:
    assert ccv.check("1.0", _data("3.40", "3.30", enabled=False)) == []
