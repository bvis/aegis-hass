"""Every platform sets PARALLEL_UPDATES (#606)."""

from __future__ import annotations

import importlib

import pytest

from custom_components.aegis_ajax import PLATFORMS

READ_ONLY = {"binary_sensor", "event", "sensor", "update"}


@pytest.mark.parametrize("platform", [p.value for p in PLATFORMS])
def test_platform_sets_parallel_updates(platform: str) -> None:
    module = importlib.import_module(f"custom_components.aegis_ajax.{platform}")
    # Action platforms run one call at a time so Ajax never gets more requests than the app sends.
    expected = 0 if platform in READ_ONLY else 1
    assert expected == module.PARALLEL_UPDATES
