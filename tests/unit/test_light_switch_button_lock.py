"""Tests for the LightSwitch "Lock switch buttons" switch (#641)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.aegis_ajax.api.hts.messages import MsgType, tlv_decode, tlv_encode
from custom_components.aegis_ajax.api.models import Device
from custom_components.aegis_ajax.const import DeviceState
from custom_components.aegis_ajax.switch import AjaxLightSwitchButtonLockSwitch, async_setup_entry
from tests.unit.hts.test_client import _make_client, _msg
from tests.unit.test_coordinator import _make_coordinator

LS = "3A1B2C4D"
HUB = "0001A2B3"


def _device(device_type: str = "light_switch_two_gang", device_id: str = LS) -> Device:
    return Device(
        id=device_id,
        hub_id=HUB,
        name="Hall",
        device_type=device_type,
        room_id=None,
        group_id=None,
        state=DeviceState.ONLINE,
        malfunctions=0,
        bypassed=False,
        statuses={},
        battery=None,
    )


def _coordinator(answer: int = 0x02):  # noqa: ANN202
    coordinator = _make_coordinator()
    coordinator.devices[LS] = _device()
    coordinator.async_set_updated_data = MagicMock()
    hts = MagicMock()
    hts.is_connected = True
    hts.write_device_parameter = AsyncMock(return_value=answer)
    coordinator._hts_client = hts
    return coordinator


class TestClientWrite:
    @pytest.mark.asyncio
    async def test_sends_the_apps_write_frame_and_returns_the_answer(self) -> None:
        client = _make_client()
        client._connected = True
        client._send_message = AsyncMock(return_value=0x000102)  # type: ignore[method-assign]

        task = asyncio.create_task(client.write_device_parameter(HUB, 0x44, LS, 0x34, b"\x07"))
        await asyncio.sleep(0)
        msg_type, payload = client._send_message.await_args.args
        assert msg_type == MsgType.WRITE_PARAMETER
        assert client._send_message.await_args.kwargs == {"receiver": 0x0001A2B3}
        assert tlv_decode(payload) == [
            b"\x44",
            bytes.fromhex(LS),
            b"\x34",
            b"\x07",
            b"\xfe\xfe",
        ]

        client._handle_answer(_msg(MsgType.ACK, tlv_encode([b"\x00", b"\x00\x01\x02"])))
        assert not task.done()
        client._handle_answer(_msg(MsgType.ACK, tlv_encode([b"\x02", b"\x00\x01\x02"])))
        assert await task == 0x02


class TestTracking:
    def test_settings_byte_is_kept_and_logged_bit_by_bit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _coordinator()
        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(HUB, LS, {0x34: b"\x05", 0x01: b"\x00"}, from_body=True)

        assert coordinator.light_switch_settings == {LS: 0x05}
        assert "backlight=1 buttons_locked=0 state_memory=1 current_threshold=0" in caplog.text
        assert "keys=0x01,0x34" in caplog.text
        coordinator.async_set_updated_data.assert_called_once()

    def test_older_family_row_is_logged_but_not_tracked(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _coordinator()
        with caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(HUB, LS, {0x34: b"\x05", 0x4B: b"\x01"})

        assert coordinator.light_switch_settings == {}
        assert "0x4b=01" in caplog.text

    def test_other_device_types_are_ignored(self) -> None:
        coordinator = _coordinator()
        coordinator.devices[LS] = _device("wall_switch")
        coordinator._on_hts_device_kv(HUB, LS, {0x34: b"\x05"})
        assert coordinator.light_switch_settings == {}


class TestWrite:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("current", "locked", "written"),
        [(0x05, True, 0x07), (0x07, False, 0x05), (0x0F, False, 0x0D), (0x00, True, 0x02)],
    )
    async def test_only_bit_1_changes(self, current: int, locked: bool, written: int) -> None:
        coordinator = _coordinator()
        coordinator.light_switch_settings[LS] = current

        await coordinator.async_set_light_switch_buttons_locked(LS, locked=locked)

        coordinator._hts_client.write_device_parameter.assert_awaited_once_with(
            HUB, 0x44, LS, 0x34, bytes([written])
        )
        assert coordinator.light_switch_settings[LS] == written

    @pytest.mark.asyncio
    async def test_nothing_sent_when_already_in_that_state(self) -> None:
        coordinator = _coordinator()
        coordinator.light_switch_settings[LS] = 0x07
        await coordinator.async_set_light_switch_buttons_locked(LS, locked=True)
        coordinator._hts_client.write_device_parameter.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nothing_sent_before_the_hub_reported_the_byte(self) -> None:
        coordinator = _coordinator()
        with pytest.raises(HomeAssistantError):
            await coordinator.async_set_light_switch_buttons_locked(LS, locked=True)
        coordinator._hts_client.write_device_parameter.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refusal_keeps_the_reported_state(self) -> None:
        coordinator = _coordinator(answer=0x05)
        coordinator.light_switch_settings[LS] = 0x05
        with pytest.raises(HomeAssistantError) as exc:
            await coordinator.async_set_light_switch_buttons_locked(LS, locked=True)
        assert exc.value.translation_key == "command_permission_denied"
        assert coordinator.light_switch_settings[LS] == 0x05

    @pytest.mark.asyncio
    async def test_hub_report_matching_the_write_is_confirmed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _coordinator()
        coordinator.light_switch_settings[LS] = 0x05
        await coordinator.async_set_light_switch_buttons_locked(LS, locked=True)

        with caplog.at_level("INFO", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(HUB, LS, {0x34: b"\x07"})

        assert "confirmed the write: settings 0x07" in caplog.text

    @pytest.mark.asyncio
    async def test_hub_report_that_differs_wins_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _coordinator()
        coordinator.light_switch_settings[LS] = 0x05
        await coordinator.async_set_light_switch_buttons_locked(LS, locked=True)

        with caplog.at_level("WARNING", logger="custom_components.aegis_ajax.coordinator"):
            coordinator._on_hts_device_kv(HUB, LS, {0x34: b"\x05"})

        assert "reported settings 0x05 after we wrote 0x07" in caplog.text
        assert coordinator.light_switch_settings[LS] == 0x05


class TestEntity:
    def _switch(self, settings: dict[str, int]) -> AjaxLightSwitchButtonLockSwitch:
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.devices = {LS: _device()}
        coordinator.light_switch_settings = settings
        coordinator.async_set_light_switch_buttons_locked = AsyncMock()
        return AjaxLightSwitchButtonLockSwitch(coordinator=coordinator, device_id=LS)

    def test_unavailable_until_the_hub_reports(self) -> None:
        assert not self._switch({}).available
        assert self._switch({LS: 0x00}).available

    def test_state_is_bit_1(self) -> None:
        assert self._switch({LS: 0x02}).is_on is True
        assert self._switch({LS: 0x0D}).is_on is False

    @pytest.mark.asyncio
    async def test_toggle_calls_the_coordinator(self) -> None:
        switch = self._switch({LS: 0x00})
        await switch.async_turn_on()
        switch.coordinator.async_set_light_switch_buttons_locked.assert_awaited_once_with(
            LS, locked=True
        )

    @pytest.mark.asyncio
    async def test_created_for_light_switches_only(self) -> None:
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.spaces = {}
        coordinator.devices = {LS: _device(), "WALL0001": _device("wall_switch", "WALL0001")}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {"bypass_switches": "never"}
        added: list = []
        with (
            patch("custom_components.aegis_ajax.switch.er.async_get", return_value=MagicMock()),
            patch(
                "custom_components.aegis_ajax.switch.er.async_entries_for_config_entry",
                return_value=[],
            ),
        ):
            await async_setup_entry(MagicMock(), entry, lambda e, *a, **k: added.extend(e))
        ids = {e.unique_id for e in added if isinstance(e, AjaxLightSwitchButtonLockSwitch)}
        assert ids == {f"aegis_ajax_{LS}_buttons_locked"}
