"""Tests for the button platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


class TestCapturePhotoButtonSetup:
    @pytest.mark.asyncio
    async def test_setup_adds_only_phod_capability(self) -> None:
        from custom_components.aegis_ajax.button import async_setup_entry

        coordinator = _make_coordinator()
        coordinator.devices = {
            "phod": Device(
                id="phod",
                hub_id="hub-1",
                name="PhOD",
                device_type="motion_cam_phod",
                room_id=None,
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={},
                battery=None,
            ),
            "not-phod": Device(
                id="not-phod",
                hub_id="hub-1",
                name="Camera",
                device_type="motion_cam",
                room_id=None,
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={},
                battery=None,
            ),
        }
        coordinator.rooms = {}
        coordinator.spaces = {}
        entry = MagicMock(runtime_data=coordinator)
        added: list[object] = []

        await async_setup_entry(MagicMock(), entry, added.extend)

        assert [entity.unique_id for entity in added] == ["aegis_ajax_phod_capture_photo"]


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
        listener.has_fcm_credentials = True
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
        """The listener is still absent during the first seconds of setup."""
        from homeassistant.exceptions import HomeAssistantError

        button, coordinator = self._make_button()
        coordinator._notification_listener = None

        with pytest.raises(HomeAssistantError) as exc:
            await button.async_press()
        assert exc.value.translation_key == "photo_no_push"

    @pytest.mark.asyncio
    async def test_no_fcm_credentials_raises_without_asking_the_hub(self) -> None:
        """A listener exists on every install; credentials are what decide (#524).

        Setup starts the listener unconditionally, so the object being there
        says nothing. Without credentials the photo can never come back, and
        the capture request must not be sent at all.
        """
        from homeassistant.exceptions import HomeAssistantError

        button, coordinator = self._make_button()
        coordinator._notification_listener.has_fcm_credentials = False

        with pytest.raises(HomeAssistantError) as exc:
            await button.async_press()
        assert exc.value.translation_key == "photo_no_push"
        coordinator._devices_api.capture_photo.assert_not_awaited()

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


def _make_siren(device_type: str = "home_siren") -> Device:
    return Device(
        id="siren-1",
        hub_id="hub-1",
        name="Siren",
        device_type=device_type,
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses={},
        battery=None,
    )


class TestSirenSoundTestButton:
    """`button.<siren>_test_sound` plays the siren's test sound (#549)."""

    @pytest.mark.asyncio
    async def test_setup_adds_one_per_addressable_siren(self) -> None:
        from custom_components.aegis_ajax.button import AjaxSirenSoundTestButton, async_setup_entry

        coordinator = _make_coordinator()
        coordinator.devices = {  # type: ignore[attr-defined]
            "siren-1": _make_siren("street_siren"),
            # No ObjectType case, so the command can't address it.
            "siren-2": _make_siren("street_siren_plus"),
        }
        coordinator.spaces = {}  # type: ignore[attr-defined]
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        sirens = [e for e in added if isinstance(e, AjaxSirenSoundTestButton)]
        assert [e.unique_id for e in sirens] == ["aegis_ajax_siren-1_siren_sound_test"]

    @pytest.mark.asyncio
    async def test_press_sends_once_and_drops_a_press_too_soon(self) -> None:
        from custom_components.aegis_ajax.button import AjaxSirenSoundTestButton

        coordinator = MagicMock()
        coordinator.devices = {"siren-1": _make_siren()}
        coordinator.devices_api.start_sound_test = AsyncMock()  # type: ignore[attr-defined]
        button = AjaxSirenSoundTestButton(coordinator, "siren-1")  # type: ignore[arg-type]

        await button.async_press()
        await button.async_press()

        coordinator.devices_api.start_sound_test.assert_awaited_once_with(  # type: ignore[attr-defined]
            "hub-1", "siren-1", "home_siren"
        )

    @pytest.mark.asyncio
    async def test_refusal_raises_translated_error(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.aegis_ajax.api.devices import DeviceCommandError
        from custom_components.aegis_ajax.button import AjaxSirenSoundTestButton

        coordinator = MagicMock()
        coordinator.devices = {"siren-1": _make_siren()}
        coordinator.devices_api.start_sound_test = AsyncMock(  # type: ignore[attr-defined]
            side_effect=DeviceCommandError(
                "sound_test: permission_denied", reason="permission_denied"
            )
        )
        button = AjaxSirenSoundTestButton(coordinator, "siren-1")  # type: ignore[arg-type]

        with pytest.raises(HomeAssistantError) as exc:
            await button.async_press()
        assert exc.value.translation_key == "command_permission_denied"
