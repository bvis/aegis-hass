"""Tests for DevicesCache (the Store-backed cache from #114)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aegis_ajax.api.models import BatteryInfo, Device
from custom_components.aegis_ajax.const import DeviceState
from custom_components.aegis_ajax.device_cache import (
    DevicesCache,
    _serialize_device,
)


def _make_device(device_id: str = "d1", **overrides: object) -> Device:
    base = {
        "id": device_id,
        "hub_id": "hub-1",
        "name": "Sensor",
        "device_type": "door_protect",
        "room_id": None,
        "group_id": None,
        "state": DeviceState.ONLINE,
        "malfunctions": 0,
        "bypassed": False,
        "statuses": {},
        "battery": None,
    }
    base.update(overrides)
    return Device(**base)  # type: ignore[arg-type]


class TestSerialization:
    def test_roundtrip_minimal_device(self) -> None:
        from custom_components.aegis_ajax.device_cache import _deserialize_device

        d = _make_device("d1")
        roundtripped = _deserialize_device(_serialize_device(d))
        assert roundtripped == d

    def test_roundtrip_device_with_battery_and_statuses(self) -> None:
        from custom_components.aegis_ajax.device_cache import _deserialize_device

        d = _make_device(
            "d2",
            battery=BatteryInfo(level=87, is_low=False),
            statuses={"co_detected": False, "temperature": 21.5},
            room_id="r1",
            group_id="g1",
            malfunctions=2,
            bypassed=True,
        )
        roundtripped = _deserialize_device(_serialize_device(d))
        assert roundtripped == d

    def test_drops_non_json_status_values(self) -> None:
        # `motion_detected_at` carries a `datetime`, which is not JSON-able.
        # The cache is best-effort first-frame restoration, so we drop
        # offending entries instead of inventing a wire format. The next
        # snapshot repopulates them within seconds.
        from custom_components.aegis_ajax.device_cache import _deserialize_device

        d = _make_device(
            "d3",
            statuses={
                "motion_detected": True,
                "motion_detected_at": datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
            },
        )
        roundtripped = _deserialize_device(_serialize_device(d))
        assert roundtripped.statuses == {"motion_detected": True}


class TestDevicesCache:
    @pytest.mark.asyncio
    async def test_load_returns_none_when_store_empty(self) -> None:
        cache = DevicesCache(MagicMock(), "entry-1")
        cache._store.async_load = AsyncMock(return_value=None)
        assert await cache.async_load() is None

    @pytest.mark.asyncio
    async def test_save_then_load_roundtrip(self) -> None:
        # Replace the Store with an in-memory fake so we exercise the real
        # serialize → save → load → deserialize path end-to-end.
        cache = DevicesCache(MagicMock(), "entry-1")
        store_state: dict[str, object] = {}

        async def fake_save(value: object) -> None:
            store_state["value"] = value

        async def fake_load() -> object:
            return store_state.get("value")

        cache._store.async_save = fake_save  # type: ignore[method-assign]
        cache._store.async_load = fake_load  # type: ignore[method-assign]

        d = _make_device("d1", battery=BatteryInfo(level=42, is_low=True))
        await cache.async_save({"d1": d})
        loaded = await cache.async_load()
        assert loaded == {"d1": d}

    @pytest.mark.asyncio
    async def test_load_returns_none_on_corrupt_payload(self) -> None:
        # A schema bump or a manually-edited storage file should never crash
        # the integration — fall back to `None` (no cache) so the heavy
        # first-refresh path runs.
        cache = DevicesCache(MagicMock(), "entry-1")
        cache._store.async_load = AsyncMock(  # type: ignore[method-assign]
            return_value={"devices": [{"not_a_device": True}]}
        )
        assert await cache.async_load() is None

    @pytest.mark.asyncio
    async def test_storage_key_is_per_entry(self) -> None:
        # Two entries (e.g. multiple Ajax accounts) must not share a cache.
        from custom_components.aegis_ajax.device_cache import _storage_key

        assert _storage_key("entry-a") != _storage_key("entry-b")

    def test_schedule_save_debounces_through_store(self) -> None:
        # `async_schedule_save` must hand a payload-builder + delay to
        # the underlying Store so bursts of stream snapshots collapse
        # into a single disk write.
        from custom_components.aegis_ajax.device_cache import _SAVE_DEBOUNCE_SECONDS

        cache = DevicesCache(MagicMock(), "entry-1")
        cache._store.async_delay_save = MagicMock()  # type: ignore[method-assign]

        d1 = _make_device("d1")
        cache.async_schedule_save({"d1": d1})

        cache._store.async_delay_save.assert_called_once()
        args, _ = cache._store.async_delay_save.call_args
        data_func, delay = args
        assert delay == _SAVE_DEBOUNCE_SECONDS
        # Builder is lazy: it reads from `_pending` at flush time, so a
        # later snapshot replacing `_pending` is what gets written.
        d2 = _make_device("d2")
        cache.async_schedule_save({"d2": d2})
        payload = data_func()
        assert [entry["id"] for entry in payload["devices"]] == ["d2"]
