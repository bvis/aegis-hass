"""What the hub's battery deltas actually contain (#506).

`charge_level_percentage` is a plain proto3 scalar, so "absent" and "zero"
are the same bytes and the delta handler applies only the fields it can
prove are present. Whether a real delta carries a level at all is unknown —
no capture of one exists — and the interesting outcome is the silent one:
deltas that arrive and never carry a level, which leaves the reading as
stale as it was before the fix, with nothing anywhere to say so.

These counts exist so the answer arrives in any diagnostics dump instead of
depending on someone running DEBUG at the moment a battery moves.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.device_cache import BatteryDeltaShapes
from tests.unit.test_coordinator import _coordinator_with_stream, _make_device


def _coordinator() -> Any:  # noqa: ANN401
    coordinator = _coordinator_with_stream()
    coordinator.devices["d1"] = _make_device("d1")
    return coordinator


def _make_shapes() -> BatteryDeltaShapes:
    with patch("custom_components.aegis_ajax.device_cache.Store") as store_cls:
        store_cls.return_value = MagicMock()
        return BatteryDeltaShapes(MagicMock(), "entry-1")


class TestCounting:
    def test_a_full_delta_counts_level_and_state(self) -> None:
        shapes = _make_shapes()

        shapes.note({"level": 20, "is_low": True})

        assert shapes.as_dict() == {
            "received": 1,
            "with_level": 1,
            "with_state": 1,
            "carried_nothing": 0,
        }

    def test_an_alert_only_delta_counts_no_level(self) -> None:
        """The shape that makes the fix a no-op: the hub says "alert" and
        never restates the percentage, so the level stays where it was."""
        shapes = _make_shapes()

        shapes.note({"is_low": True})

        counts = shapes.as_dict()
        assert counts["received"] == 1
        assert counts["with_level"] == 0
        assert counts["with_state"] == 1

    def test_a_delta_carrying_nothing_is_counted_as_such(self) -> None:
        shapes = _make_shapes()

        shapes.note({})

        counts = shapes.as_dict()
        assert counts["received"] == 1
        assert counts["carried_nothing"] == 1
        assert counts["with_level"] == 0

    def test_noting_schedules_a_debounced_save(self) -> None:
        """Counts must outlive a restart: a counter that resets is exactly
        what kept a month of undelivered push invisible in #437."""
        shapes = _make_shapes()

        shapes.note({"level": 55})

        shapes._store.async_delay_save.assert_called_once()
        saved = shapes._store.async_delay_save.call_args[0][0]()
        assert saved["with_level"] == 1


class TestLoading:
    @pytest.mark.asyncio
    async def test_a_stored_record_is_restored(self) -> None:
        shapes = _make_shapes()
        shapes._store.async_load = AsyncMock(
            return_value={
                "received": 4,
                "with_level": 3,
                "with_state": 4,
                "carried_nothing": 0,
            }
        )

        await shapes.async_load()

        assert shapes.as_dict()["received"] == 4
        assert shapes.as_dict()["with_level"] == 3

    @pytest.mark.asyncio
    async def test_no_stored_record_leaves_zeros(self) -> None:
        shapes = _make_shapes()
        shapes._store.async_load = AsyncMock(return_value=None)

        await shapes.async_load()

        assert shapes.as_dict()["received"] == 0

    @pytest.mark.asyncio
    async def test_a_garbled_record_is_ignored_key_by_key(self) -> None:
        """Tolerate what is unreadable, keep what is not — a bad value for
        one counter must not throw away the others."""
        shapes = _make_shapes()
        shapes._store.async_load = AsyncMock(
            return_value={"received": 7, "with_level": "three", "unknown": 1}
        )

        await shapes.async_load()

        counts = shapes.as_dict()
        assert counts["received"] == 7
        assert counts["with_level"] == 0
        assert "unknown" not in counts

    @pytest.mark.asyncio
    async def test_a_storage_failure_never_raises(self) -> None:
        """A storage problem must not stop the integration — #437(a)'s rule."""
        shapes = _make_shapes()
        shapes._store.async_load = AsyncMock(side_effect=OSError("boom"))

        await shapes.async_load()

        assert shapes.as_dict()["received"] == 0


class TestTheDumpCarriesIt:
    @pytest.mark.asyncio
    async def test_the_counts_reach_the_diagnostics_dump(self) -> None:
        """An answer nobody can read is not recorded (#500's lesson)."""
        from custom_components.aegis_ajax.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        coordinator = _coordinator()
        coordinator._battery_shapes = MagicMock()
        coordinator._battery_shapes.as_dict.return_value = {
            "received": 3,
            "with_level": 0,
            "with_state": 3,
            "carried_nothing": 0,
        }
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.data = {}
        entry.options = {}

        dump = await async_get_config_entry_diagnostics(MagicMock(), entry)

        assert dump["battery_delta_shapes"]["received"] == 3
        assert dump["battery_delta_shapes"]["with_level"] == 0


class TestTheCoordinatorRecordsWhatItSaw:
    def test_a_battery_delta_is_noted_with_what_it_carried(self) -> None:
        coordinator = _coordinator()
        noted: list[dict[str, Any]] = []
        coordinator._battery_shapes = MagicMock()
        coordinator._battery_shapes.note = noted.append

        coordinator._handle_status_update(
            "d1", "battery", {"op": 2, "battery": {"level": 20, "is_low": True}}
        )

        assert noted == [{"level": 20, "is_low": True}]

    def test_a_battery_delta_carrying_nothing_is_still_noted(self) -> None:
        coordinator = _coordinator()
        noted: list[dict[str, Any]] = []
        coordinator._battery_shapes = MagicMock()
        coordinator._battery_shapes.note = noted.append

        coordinator._handle_status_update("d1", "battery", {"op": 2})

        assert noted == [{}]

    def test_a_non_battery_delta_is_not_noted(self) -> None:
        coordinator = _coordinator()
        noted: list[dict[str, Any]] = []
        coordinator._battery_shapes = MagicMock()
        coordinator._battery_shapes.note = noted.append

        coordinator._handle_status_update("d1", "door_opened", {"op": 1})

        assert noted == []
