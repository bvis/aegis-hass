"""Tests for the shared entity helpers (build_device_info)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.aegis_ajax.api.models import Device, Room
from custom_components.aegis_ajax.const import DOMAIN, DeviceState
from custom_components.aegis_ajax.entity import (
    async_get_registered_device,
    build_device_info,
    via_device_fields,
)


def _make_device(
    *,
    device_type: str = "door_protect",
    room_id: str | None = None,
    device_id: str = "ABC123",
    hub_id: str = "HUB001",
) -> Device:
    return Device(
        id=device_id,
        hub_id=hub_id,
        name="Front Door",
        device_type=device_type,
        room_id=room_id,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses={},
        battery=None,
    )


class TestBuildDeviceInfo:
    def test_includes_device_id_as_serial_number(self) -> None:
        info = build_device_info(_make_device(device_id="DEV42"))
        assert info["serial_number"] == "DEV42"

    def test_identifiers_use_device_id(self) -> None:
        info = build_device_info(_make_device(device_id="DEV42"))
        assert (DOMAIN, "DEV42") in info["identifiers"]

    def test_non_hub_device_links_to_hub_by_registry_id_on_new_ha(self) -> None:
        # HA 2026.8+ (#444): `via_device` is deprecated and removed in 2027.8;
        # the replacement `via_device_id` takes the hub's registry entry id.
        with patch("custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED", True):
            info = build_device_info(
                _make_device(device_type="door_protect", hub_id="HUB7"), via_device_id="reg-hub7"
            )
        assert info["via_device_id"] == "reg-hub7"
        assert "via_device" not in info

    def test_link_is_omitted_when_hub_not_yet_registered_on_new_ha(self) -> None:
        # A `via_device_id` that is not a registered device id makes HA reject
        # the whole DeviceInfo (and the entity with it), so no id → no link.
        with patch("custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED", True):
            info = build_device_info(
                _make_device(device_type="door_protect", hub_id="HUB7"), via_device_id=None
            )
        assert "via_device_id" not in info
        assert "via_device" not in info

    def test_video_edge_channel_has_no_self_via_link_on_new_ha(self) -> None:
        # Video Edge channels have no hub-side parent; their parser uses the
        # channel id as hub_id. Passing its own registered id as via_device_id
        # makes HA reject the child device as its own parent (#489).
        with patch("custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED", True):
            info = build_device_info(
                _make_device(
                    device_type="video_edge_turret",
                    device_id="camera-1",
                    hub_id="camera-1",
                ),
                via_device_id="reg-camera-1",
            )
        assert "via_device_id" not in info
        assert "via_device" not in info

    def test_non_hub_device_keeps_identifier_link_on_old_ha(self) -> None:
        # Before 2026.8 `DeviceInfo` has no `via_device_id` key and passing one
        # is a TypeError inside HA, so the identifier tuple stays in use there.
        with patch("custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED", False):
            info = build_device_info(
                _make_device(device_type="door_protect", hub_id="HUB7"), via_device_id="reg-hub7"
            )
        assert info["via_device"] == (DOMAIN, "HUB7")
        assert "via_device_id" not in info

    def test_video_edge_channel_has_no_self_via_link_on_old_ha(self) -> None:
        with patch("custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED", False):
            info = build_device_info(
                _make_device(
                    device_type="video_edge_turret",
                    device_id="camera-1",
                    hub_id="camera-1",
                ),
                via_device_id="reg-camera-1",
            )
        assert "via_device_id" not in info
        assert "via_device" not in info

    def test_hub_device_has_no_via_link(self) -> None:
        for supported in (True, False):
            with patch("custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED", supported):
                info = build_device_info(
                    _make_device(device_type="hub_two_4g", device_id="HUB7"),
                    via_device_id="reg-hub7",
                )
            assert "via_device" not in info
            assert "via_device_id" not in info

    def test_suggested_area_set_from_room(self) -> None:
        rooms = {"r1": Room(id="r1", name="Kitchen", space_id="s1")}
        info = build_device_info(_make_device(room_id="r1"), rooms)
        assert info["suggested_area"] == "Kitchen"

    def test_no_suggested_area_when_room_id_missing(self) -> None:
        rooms = {"r1": Room(id="r1", name="Kitchen", space_id="s1")}
        info = build_device_info(_make_device(room_id=None), rooms)
        assert "suggested_area" not in info

    def test_no_suggested_area_when_room_not_in_map(self) -> None:
        rooms = {"r1": Room(id="r1", name="Kitchen", space_id="s1")}
        info = build_device_info(_make_device(room_id="r2"), rooms)
        assert "suggested_area" not in info

    def test_no_suggested_area_when_rooms_omitted(self) -> None:
        info = build_device_info(_make_device(room_id="r1"))
        assert "suggested_area" not in info

    def test_model_humanized_from_device_type(self) -> None:
        info = build_device_info(_make_device(device_type="motion_protect_outdoor"))
        assert info["model"] == "Motion Protect Outdoor"

    def test_sw_version_set_from_firmware_version(self) -> None:
        info = build_device_info(
            _make_device(device_type="hub_two_4g", device_id="HUB7"),
            firmware_version="2.41.116",
        )
        assert info["sw_version"] == "2.41.116"

    def test_no_sw_version_when_firmware_version_omitted(self) -> None:
        info = build_device_info(_make_device(device_type="hub_two_4g", device_id="HUB7"))
        assert "sw_version" not in info

    def test_no_sw_version_when_firmware_version_empty(self) -> None:
        info = build_device_info(
            _make_device(device_type="hub_two_4g", device_id="HUB7"),
            firmware_version="",
        )
        assert "sw_version" not in info


class TestAsyncSendDeviceCommand:
    """Maps Ajax command failures to clear, translated HomeAssistantErrors."""

    def _coordinator(self) -> object:
        from unittest.mock import AsyncMock, MagicMock

        coordinator = MagicMock()
        coordinator.devices_api.send_command = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        return coordinator

    async def test_success_sends_and_refreshes(self) -> None:
        from custom_components.aegis_ajax.entity import async_send_device_command

        coordinator = self._coordinator()
        cmd = object()

        await async_send_device_command(coordinator, cmd)

        coordinator.devices_api.send_command.assert_awaited_once_with(cmd)
        coordinator.async_request_refresh.assert_awaited_once()

    async def test_permission_denied_maps_to_translation_key(self) -> None:
        from unittest.mock import AsyncMock

        import pytest  # noqa: PLC0415
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.aegis_ajax.api.devices import DeviceCommandError
        from custom_components.aegis_ajax.entity import async_send_device_command

        coordinator = self._coordinator()
        coordinator.devices_api.send_command = AsyncMock(
            side_effect=DeviceCommandError("bypass: permission_denied", reason="permission_denied")
        )

        with pytest.raises(HomeAssistantError) as exc:
            await async_send_device_command(coordinator, object())

        assert exc.value.translation_key == "command_permission_denied"
        assert exc.value.translation_domain == DOMAIN
        # No refresh on failure
        coordinator.async_request_refresh.assert_not_called()

    async def test_unknown_reason_falls_back_with_placeholder(self) -> None:
        from unittest.mock import AsyncMock

        import pytest  # noqa: PLC0415
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.aegis_ajax.api.devices import DeviceCommandError
        from custom_components.aegis_ajax.entity import async_send_device_command

        coordinator = self._coordinator()
        coordinator.devices_api.send_command = AsyncMock(
            side_effect=DeviceCommandError("on: weird_new_code", reason="weird_new_code")
        )

        with pytest.raises(HomeAssistantError) as exc:
            await async_send_device_command(coordinator, object())

        assert exc.value.translation_key == "command_failed"
        assert exc.value.translation_placeholders == {"reason": "weird_new_code"}


class TestViaDeviceFields:
    """The keyfob device builds its own DeviceInfo; it shares the via logic."""

    def test_new_ha_uses_registry_id(self) -> None:
        with patch("custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED", True):
            assert via_device_fields("HUB7", "reg-hub7") == {"via_device_id": "reg-hub7"}
            assert via_device_fields("HUB7", None) == {}

    def test_old_ha_uses_identifier_tuple(self) -> None:
        with patch("custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED", False):
            assert via_device_fields("HUB7", "reg-hub7") == {"via_device": (DOMAIN, "HUB7")}
            assert via_device_fields("HUB7", None) == {"via_device": (DOMAIN, "HUB7")}


class TestAsyncGetRegisteredDevice:
    """`async_get_device(identifiers=...)` is deprecated in 2026.9 and removed
    in 2027.8 (#444); the replacement is scoped to the config entry."""

    def test_prefers_the_config_entry_scoped_lookup(self) -> None:
        registry = MagicMock()
        registry.async_get_device_by_identifier.return_value = "entry"

        found = async_get_registered_device(registry, (DOMAIN, "HUB7"), "cfg-1")

        assert found == "entry"
        registry.async_get_device_by_identifier.assert_called_once_with((DOMAIN, "HUB7"), "cfg-1")
        registry.async_get_device.assert_not_called()

    def test_falls_back_to_the_legacy_lookup_on_old_ha(self) -> None:
        registry = MagicMock(spec=["async_get_device"])
        registry.async_get_device.return_value = "entry"

        found = async_get_registered_device(registry, (DOMAIN, "HUB7"), "cfg-1")

        assert found == "entry"
        registry.async_get_device.assert_called_once_with(identifiers={(DOMAIN, "HUB7")})


class TestNoDeviceIsEverItsOwnParent:
    """#489, generalised: the self-parent bug, caught for every family.

    The Video Edge regression was not really about Video Edge. It was about a
    device whose `hub_id` equals its own id — which the parser does deliberately
    whenever Ajax gives a device no hub parent — being handed its own registry
    entry as `via_device_id`. Home Assistant refuses a device that is its own
    parent and stops the platform providing its entities, which is how two
    releases shipped with the NVR-bridged cameras silently orphaned.

    A test naming `video_edge_turret` would not have caught it in advance. This
    one asserts the property instead, over every device family the registry
    knows plus an unmapped one, on both device-registry APIs.

    ⚠️ Neither branch can be run against a Home Assistant that actually has the
    2026.8 API: the newest `homeassistant` on PyPI is 2026.2.x, so CI resolves a
    core where `via_device_id` does not exist in `DeviceInfo` and
    `_VIA_DEVICE_ID_SUPPORTED` is always False. Patching the flag is therefore
    the only way to exercise the branch every modern user actually runs, which
    is exactly why the invariant has to be asserted rather than assumed.
    """

    @staticmethod
    def _families() -> list[str]:
        from custom_components.aegis_ajax.device_handlers import _DEVICE_HANDLERS

        return [*sorted(_DEVICE_HANDLERS), "unmapped_unknown_device"]

    @pytest.mark.parametrize("via_device_id_supported", [True, False])
    def test_a_parentless_device_gets_no_via_link_at_all(
        self, *, via_device_id_supported: bool
    ) -> None:
        for device_type in self._families():
            device = _make_device(device_type=device_type, device_id="SELF1", hub_id="SELF1")
            with patch(
                "custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED",
                via_device_id_supported,
            ):
                info = build_device_info(device, via_device_id="reg-SELF1")
            assert "via_device_id" not in info, device_type
            assert "via_device" not in info, device_type

    @pytest.mark.parametrize("via_device_id_supported", [True, False])
    def test_a_device_with_a_real_hub_keeps_its_link(
        self, *, via_device_id_supported: bool
    ) -> None:
        """The guard must not cost the ordinary case its parent."""
        device = _make_device(device_type="door_protect", device_id="DEV1", hub_id="HUB1")
        with patch(
            "custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED",
            via_device_id_supported,
        ):
            info = build_device_info(device, via_device_id="reg-HUB1")
        if via_device_id_supported:
            assert info["via_device_id"] == "reg-HUB1"
        else:
            assert info["via_device"] == (DOMAIN, "HUB1")

    def test_a_via_link_never_points_at_the_devices_own_identity(self) -> None:
        """The invariant stated directly, whichever key carries the link."""
        for device_type in self._families():
            for hub_id, registry_id in (("SELF1", "reg-SELF1"), ("HUB1", "reg-HUB1")):
                device = _make_device(device_type=device_type, device_id="SELF1", hub_id=hub_id)
                for supported in (True, False):
                    with patch(
                        "custom_components.aegis_ajax.entity._VIA_DEVICE_ID_SUPPORTED", supported
                    ):
                        info = build_device_info(device, via_device_id=registry_id)
                    assert info.get("via_device") != (DOMAIN, device.id), (device_type, supported)
                    assert info.get("via_device_id") != "reg-SELF1" or hub_id != "SELF1", (
                        device_type,
                        supported,
                    )


class TestFeatureDetectionMatchesTheInstalledCore:
    """`_VIA_DEVICE_ID_SUPPORTED` is a claim about the running Home Assistant.

    If the detection ever stops matching reality — HA renames the key, or the
    `TypedDict` stops exposing `__optional_keys__` — every via link silently
    takes the wrong branch, and on a modern core that means `via_device` tuples
    HA has removed. Asserting it against the installed core is cheap and it is
    the one part of #444 that CI *can* verify at whatever version it resolves.
    """

    def test_detection_agrees_with_device_info(self) -> None:
        from homeassistant.helpers.device_registry import DeviceInfo

        from custom_components.aegis_ajax import entity

        actual = "via_device_id" in (DeviceInfo.__required_keys__ | DeviceInfo.__optional_keys__)
        assert entity._VIA_DEVICE_ID_SUPPORTED is actual

    def test_the_old_key_still_exists_while_we_fall_back_to_it(self) -> None:
        """The fallback branch is only safe while HA still accepts `via_device`."""
        from homeassistant.helpers.device_registry import DeviceInfo

        from custom_components.aegis_ajax import entity

        if entity._VIA_DEVICE_ID_SUPPORTED:
            return
        assert "via_device" in (DeviceInfo.__required_keys__ | DeviceInfo.__optional_keys__)
