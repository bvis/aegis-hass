"""Tests for the button platform."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _stub_camera_module() -> None:
    """Stub `homeassistant.components.camera` so importing button.py does not
    drag in HA's stream component (which needs numpy, not in the dev image).
    Same trick as `tests/unit/test_camera.py`."""
    if "homeassistant.components.camera" not in sys.modules:
        camera_mod = ModuleType("homeassistant.components.camera")

        class Camera:
            def __init__(self) -> None:
                pass

        camera_mod.Camera = Camera  # type: ignore[attr-defined]
        sys.modules["homeassistant.components.camera"] = camera_mod


_stub_camera_module()

from custom_components.aegis_ajax.api.models import (  # noqa: E402
    Device,
    Space,
)
from custom_components.aegis_ajax.const import (  # noqa: E402
    ConnectionStatus,
    DeviceState,
    SecurityState,
)


def _make_hub_device(device_id: str = "hub-1") -> Device:
    return Device(
        id=device_id,
        hub_id=device_id,
        name="Hub",
        device_type="hub_2",
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses={},
        battery=None,
    )


def _make_space(space_id: str = "s1", hub_id: str = "hub-1") -> Space:
    return Space(
        id=space_id,
        hub_id=hub_id,
        name="Home",
        security_state=SecurityState.DISARMED,
        connection_status=ConnectionStatus.ONLINE,
        malfunctions_count=0,
    )


def _make_coordinator() -> object:
    from custom_components.aegis_ajax.coordinator import AjaxCobrandedCoordinator

    hass = MagicMock()
    client = MagicMock()
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coordinator = AjaxCobrandedCoordinator(
            hass=hass, client=client, space_ids=["s1"], poll_interval=30
        )
    coordinator.hass = hass
    return coordinator


class TestRefreshHubButtonSetup:
    """`async_setup_entry` creates one refresh button per hub."""

    @pytest.mark.asyncio
    async def test_one_button_per_hub(self) -> None:
        from custom_components.aegis_ajax.button import (
            AjaxRefreshHubButton,
            async_setup_entry,
        )

        coordinator = _make_coordinator()
        coordinator.spaces = {
            "s1": _make_space("s1", "hub-1"),
            "s2": _make_space("s2", "hub-2"),
        }
        coordinator.devices = {
            "hub-1": _make_hub_device("hub-1"),
            "hub-2": _make_hub_device("hub-2"),
        }

        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list[object] = []

        await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        refresh_buttons = [e for e in added if isinstance(e, AjaxRefreshHubButton)]
        assert len(refresh_buttons) == 2
        assert {b._hub_id for b in refresh_buttons} == {"hub-1", "hub-2"}

    @pytest.mark.asyncio
    async def test_dedupes_when_two_spaces_share_a_hub(self) -> None:
        from custom_components.aegis_ajax.button import (
            AjaxRefreshHubButton,
            async_setup_entry,
        )

        coordinator = _make_coordinator()
        coordinator.spaces = {
            "s1": _make_space("s1", "hub-1"),
            "s2": _make_space("s2", "hub-1"),
        }
        coordinator.devices = {"hub-1": _make_hub_device("hub-1")}

        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list[object] = []

        await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        refresh_buttons = [e for e in added if isinstance(e, AjaxRefreshHubButton)]
        assert len(refresh_buttons) == 1

    @pytest.mark.asyncio
    async def test_skips_hub_with_no_device_record_yet(self) -> None:
        """First refresh races: a Space's hub_id may not yet be in `devices`."""
        from custom_components.aegis_ajax.button import (
            AjaxRefreshHubButton,
            async_setup_entry,
        )

        coordinator = _make_coordinator()
        coordinator.spaces = {"s1": _make_space("s1", "hub-1")}
        coordinator.devices = {}  # snapshot not yet populated

        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list[object] = []

        await async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        assert not any(isinstance(e, AjaxRefreshHubButton) for e in added)


class TestRefreshHubButtonPress:
    """Pressing the button dispatches through the coordinator guard."""

    def _make_button(self) -> tuple[object, object]:
        from custom_components.aegis_ajax.button import AjaxRefreshHubButton

        coordinator = _make_coordinator()
        coordinator.spaces = {"s1": _make_space("s1", "hub-1")}
        coordinator.devices = {"hub-1": _make_hub_device("hub-1")}
        coordinator.async_request_manual_refresh = AsyncMock()
        button = AjaxRefreshHubButton(coordinator=coordinator, hub_id="hub-1")
        return button, coordinator

    @pytest.mark.asyncio
    async def test_press_calls_coordinator(self) -> None:
        button, coordinator = self._make_button()

        await button.async_press()

        coordinator.async_request_manual_refresh.assert_awaited_once_with("hub-1")

    @pytest.mark.asyncio
    async def test_press_propagates_coordinator_error(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        button, coordinator = self._make_button()
        coordinator.async_request_manual_refresh.side_effect = HomeAssistantError(
            translation_domain="aegis_ajax",
            translation_key="manual_refresh_rate_limited",
            translation_placeholders={"seconds": "42"},
        )

        with pytest.raises(HomeAssistantError) as exc:
            await button.async_press()
        assert exc.value.translation_key == "manual_refresh_rate_limited"

    def test_unavailable_when_hts_is_down(self) -> None:
        button, coordinator = self._make_button()
        coordinator._hts_client = None
        assert button.available is False

    def test_available_when_hts_is_up(self) -> None:
        button, coordinator = self._make_button()
        coordinator._hts_client = MagicMock()
        assert button.available is True

    def test_unique_id_is_per_hub(self) -> None:
        button, _ = self._make_button()
        assert button.unique_id == "aegis_ajax_hub-1_refresh_hub"


class TestCapturePhotoButtonFailures:
    """A photo capture that doesn't complete must surface to the user (#193)."""

    def _make_button(self) -> tuple[object, object]:
        from custom_components.aegis_ajax.button import AjaxCapturePhotoButton

        coordinator = _make_coordinator()
        coordinator.devices = {
            "cam-1": Device(
                id="cam-1",
                hub_id="hub-1",
                name="Hallway Cam",
                device_type="motion_cam_phod",
                room_id=None,
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={},
                battery=None,
            )
        }
        coordinator.rooms = {}
        coordinator.last_photo_urls = {}
        coordinator._devices_api = MagicMock()
        coordinator._devices_api.capture_photo = AsyncMock(return_value=True)
        coordinator._media_api = MagicMock()
        coordinator._media_api.get_photo_url = AsyncMock(return_value="http://x/p.jpg")
        listener = MagicMock()
        listener.wait_for_notification_id = AsyncMock(return_value="notif-1")
        coordinator._notification_listener = listener
        button = AjaxCapturePhotoButton(
            coordinator=coordinator,
            device_id="cam-1",
            hub_id="hub-1",
            device_type="motion_cam_phod",
        )
        button.hass = MagicMock()
        return button, coordinator

    @pytest.mark.asyncio
    async def test_capture_not_accepted_raises(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        button, coordinator = self._make_button()
        coordinator._devices_api.capture_photo = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError) as exc:
            await button.async_press()
        assert exc.value.translation_key == "photo_capture_failed"

    @pytest.mark.asyncio
    async def test_no_push_listener_raises(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        button, coordinator = self._make_button()
        coordinator._notification_listener = None

        with pytest.raises(HomeAssistantError) as exc:
            await button.async_press()
        assert exc.value.translation_key == "photo_no_push"

    @pytest.mark.asyncio
    async def test_notification_timeout_raises(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        button, coordinator = self._make_button()
        coordinator._notification_listener.wait_for_notification_id = AsyncMock(return_value=None)

        with pytest.raises(HomeAssistantError) as exc:
            await button.async_press()
        assert exc.value.translation_key == "photo_capture_timeout"

    @pytest.mark.asyncio
    async def test_no_url_raises(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        button, coordinator = self._make_button()
        coordinator._media_api.get_photo_url = AsyncMock(return_value=None)

        with pytest.raises(HomeAssistantError) as exc:
            await button.async_press()
        assert exc.value.translation_key == "photo_capture_failed"
