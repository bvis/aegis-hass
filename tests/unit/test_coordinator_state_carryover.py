"""What the coordinator keeps, replaces and passes on when state arrives piece by piece."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.aegis_ajax import coordinator as module
from custom_components.aegis_ajax.api.models import (
    BatteryInfo,
    ChimeStatus,
    Device,
    DeviceState,
    SecurityState,
)
from tests.unit.test_coordinator import _coordinator_with_stream, _make_device, _make_space

_ARMED = next(state for state in SecurityState if state is not SecurityState.DISARMED)
_CHIME = next(status for status in ChimeStatus if status is not ChimeStatus.UNSPECIFIED)


def _full_device() -> Device:
    """A device with no default-looking field, so a dropped one shows."""
    return Device(
        id="d1",
        hub_id="hub-9",
        name="Hall door",
        device_type="door_protect_plus",
        room_id="room-3",
        group_id="group-2",
        state=DeviceState.ONLINE,
        malfunctions=4,
        bypassed=True,
        statuses={"tamper": True},
        battery=BatteryInfo(level=55, is_low=False),
    )


def test_a_status_update_keeps_everything_else_about_the_device() -> None:
    coordinator = _coordinator_with_stream()
    device = _full_device()
    coordinator.devices["d1"] = device

    coordinator._handle_status_update("d1", "door_opened", {"op": 1})

    assert coordinator.devices["d1"] == replace(
        device, statuses={"tamper": True, "door_opened": True}
    )
    coordinator.async_set_updated_data.assert_called_once_with(
        {"spaces": coordinator.spaces, "devices": coordinator.devices}
    )


async def _refreshed(coordinator: module.AjaxCobrandedCoordinator, *listed: object) -> dict:
    coordinator._spaces_api = MagicMock()
    coordinator._spaces_api.list_spaces = AsyncMock(return_value=list(listed))
    loop = MagicMock()
    loop.time.return_value = 1000.0
    with patch.object(module.asyncio, "get_running_loop", return_value=loop):
        return await coordinator._refresh_spaces()


async def test_an_optimistic_arm_state_overrides_the_poll_until_it_expires() -> None:
    coordinator = _coordinator_with_stream()
    coordinator._optimistic_space_states["s1"] = (1000.5, _ARMED)
    spaces = await _refreshed(coordinator, _make_space("s1"))
    assert spaces == {"s1": replace(_make_space("s1"), security_state=_ARMED)}
    assert coordinator._optimistic_space_states == {"s1": (1000.5, _ARMED)}

    # At its deadline the optimistic state is dropped and the poll wins.
    coordinator._optimistic_space_states["s1"] = (1000.0, _ARMED)
    spaces = await _refreshed(coordinator, _make_space("s1"))
    assert spaces == {"s1": _make_space("s1")}
    assert coordinator._optimistic_space_states == {}


async def test_a_poll_only_returns_this_entry_spaces() -> None:
    coordinator = _coordinator_with_stream()
    spaces = await _refreshed(coordinator, _make_space("other"), _make_space("s1"))
    assert spaces == {"s1": _make_space("s1")}


async def test_a_poll_keeps_what_only_the_hourly_snapshot_delivers() -> None:
    coordinator = _coordinator_with_stream()
    company, group = MagicMock(name="company"), MagicMock(name="group")
    coordinator.spaces["s1"] = replace(
        _make_space("s1"),
        monitoring_companies=(company,),
        monitoring_companies_loaded=True,
        groups=(group,),
        group_mode_enabled=True,
        night_mode_enabled=True,
        chime_status=_CHIME,
    )
    polled = replace(_make_space("s1"), security_state=_ARMED, malfunctions_count=2)

    spaces = await _refreshed(coordinator, polled)

    assert spaces == {
        "s1": replace(
            polled,
            monitoring_companies=(company,),
            monitoring_companies_loaded=True,
            groups=(group,),
            group_mode_enabled=True,
            night_mode_enabled=True,
            chime_status=_CHIME,
        )
    }


async def test_a_poll_does_not_invent_what_was_never_loaded() -> None:
    coordinator = _coordinator_with_stream()
    coordinator.spaces["s1"] = _make_space("s1")
    polled = replace(_make_space("s1"), chime_status=_CHIME)
    assert await _refreshed(coordinator, polled) == {"s1": polled}


def _dependent(*, loaded: bool) -> tuple[module.AjaxCobrandedCoordinator, MagicMock]:
    coordinator = _coordinator_with_stream()
    primary = MagicMock()
    primary.spaces = {"s1": _make_space("s1")}
    primary.devices = {"d1": _make_device("d1")}
    primary.rooms = {"r1": MagicMock()}
    primary.alarmed_space_ids = {"s1"}
    primary.delay_overlays = {"s1": MagicMock()}
    primary.delay_panel_states = {"s1": MagicMock()}
    coordinator._primary_coordinator = primary
    with patch.object(
        type(coordinator), "primary_loaded", new_callable=PropertyMock, return_value=loaded
    ):
        coordinator._copy_primary_state()
    return coordinator, primary


def test_a_dependent_entry_shares_the_primary_state() -> None:
    coordinator, primary = _dependent(loaded=True)
    for name in (
        "spaces",
        "devices",
        "rooms",
        "alarmed_space_ids",
        "delay_overlays",
        "delay_panel_states",
    ):
        assert getattr(coordinator, name) is getattr(primary, name), name
    coordinator.async_set_updated_data.assert_called_once_with(
        {"spaces": primary.spaces, "devices": primary.devices}
    )


def test_a_dependent_entry_is_emptied_while_the_primary_is_not_loaded() -> None:
    coordinator, _ = _dependent(loaded=False)
    assert (coordinator.spaces, coordinator.devices, coordinator.rooms) == ({}, {}, {})
    assert coordinator.alarmed_space_ids == set()
    assert coordinator.delay_overlays == {}
    coordinator.async_set_updated_data.assert_called_once_with({"spaces": {}, "devices": {}})


def test_a_motion_push_turns_motion_on_stamps_it_and_schedules_the_auto_off() -> None:
    coordinator = _coordinator_with_stream()
    device = _full_device()
    coordinator.devices["d1"] = device
    earlier = MagicMock()
    coordinator._motion_off_cancels["d1"] = earlier
    with (
        patch("time.time", return_value=1_700_000_123.9),
        patch("homeassistant.helpers.event.async_call_later") as call_later,
    ):
        coordinator.apply_push_device_motion("d1")

    assert coordinator.devices["d1"] == replace(
        device,
        statuses={"tamper": True, "motion_detected": True, "motion_detected_at": 1_700_000_123},
    )
    coordinator.async_set_updated_data.assert_called_once_with(
        {"spaces": coordinator.spaces, "devices": coordinator.devices}
    )
    # The earlier auto-off is cancelled and a new one armed.
    earlier.assert_called_once_with()
    assert call_later.call_args.args[:2] == (coordinator.hass, module.MOTION_PUSH_AUTO_OFF_SECONDS)
    assert coordinator._motion_off_cancels == {"d1": call_later.return_value}

    # When it fires, motion goes off and nothing else changes.
    coordinator.async_set_updated_data.reset_mock()
    call_later.call_args.args[2](None)
    assert coordinator.devices["d1"].statuses == {
        "tamper": True,
        "motion_detected": False,
        "motion_detected_at": 1_700_000_123,
    }
    assert coordinator._motion_off_cancels == {}
    coordinator.async_set_updated_data.assert_called_once()


def test_a_motion_push_for_an_unknown_device_does_nothing() -> None:
    coordinator = _coordinator_with_stream()
    with patch("homeassistant.helpers.event.async_call_later") as call_later:
        coordinator.apply_push_device_motion("nope")
    call_later.assert_not_called()
    coordinator.async_set_updated_data.assert_not_called()


async def test_push_listener_is_built_with_this_entry_settings() -> None:
    coordinator = _coordinator_with_stream()
    listener = MagicMock()
    listener.async_start = AsyncMock()
    with patch(
        "custom_components.aegis_ajax.notification.AjaxNotificationListener", return_value=listener
    ) as listener_cls:
        await coordinator.async_start_push_notifications(
            fcm_project_id="project",
            fcm_app_id="app",
            fcm_api_key="key",
            fcm_sender_id="sender",
            entry_id="entry-1",
            app_label="Ajax",
            disable_push_warning=True,
        )
    listener_cls.assert_called_once_with(
        hass=coordinator.hass,
        coordinator=coordinator,
        fcm_project_id="project",
        fcm_app_id="app",
        fcm_api_key="key",
        fcm_sender_id="sender",
        entry_id="entry-1",
        app_label="Ajax",
        disable_push_warning=True,
    )
    assert coordinator._notification_listener is listener

    with patch(
        "custom_components.aegis_ajax.notification.AjaxNotificationListener", return_value=listener
    ) as listener_cls:
        await coordinator.async_start_push_notifications()
    assert listener_cls.call_args.kwargs["disable_push_warning"] is False


async def test_manual_refresh_is_limited_to_one_per_hub_per_interval() -> None:
    coordinator = _coordinator_with_stream()
    coordinator._hts_client = MagicMock()
    coordinator._hts_client.request_full_status = AsyncMock()
    interval = module.MANUAL_REFRESH_INTERVAL

    with patch.object(module.time, "monotonic", return_value=5000.0):
        await coordinator.async_request_manual_refresh("hub-1")
    coordinator._hts_client.request_full_status.assert_awaited_once_with("hub-1")

    # A second press 20 s later is refused, saying how long is left.
    with (
        patch.object(module.time, "monotonic", return_value=5020.0),
        pytest.raises(HomeAssistantError) as err,
    ):
        await coordinator.async_request_manual_refresh("hub-1")
    assert err.value.translation_key == "manual_refresh_rate_limited"
    assert err.value.translation_placeholders == {"seconds": str(interval - 20)}
    assert coordinator._hts_client.request_full_status.await_count == 1

    # Another hub is not held back, and this one is free again at the interval.
    with patch.object(module.time, "monotonic", return_value=5020.0):
        await coordinator.async_request_manual_refresh("hub-2")
    with patch.object(module.time, "monotonic", return_value=5000.0 + interval):
        await coordinator.async_request_manual_refresh("hub-1")
    assert [c.args for c in coordinator._hts_client.request_full_status.await_args_list] == [
        ("hub-1",),
        ("hub-2",),
        ("hub-1",),
    ]


async def test_manual_refresh_needs_the_hub_connection() -> None:
    coordinator = _coordinator_with_stream()
    coordinator._hts_client = None
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_request_manual_refresh("hub-1")
    assert err.value.translation_key == "manual_refresh_hts_unavailable"
    assert err.value.translation_domain == module.DOMAIN
