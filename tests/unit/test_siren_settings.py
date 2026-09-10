"""Tests for the writable StreetSiren/HomeSiren settings (#310).

Covers the read path (`parse_hub_device_siren_settings` + the
`StreamHubDevice`-backed `DevicesApi.get_hub_device_siren_settings`), the write
path dispatch (`DevicesApi._update_siren_settings`), and the `number`/`select`
entities that surface the alarm duration and volume level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Wire up the proto search path before any `systems.*` import.
from custom_components.aegis_ajax import device_handlers
from custom_components.aegis_ajax.api import _proto_path as _proto_path  # noqa: F401
from custom_components.aegis_ajax.api.devices import DeviceCommandError, DevicesApi
from custom_components.aegis_ajax.api.devices_parser import parse_hub_device_siren_settings
from custom_components.aegis_ajax.api.models import Device, DeviceCommand
from custom_components.aegis_ajax.const import (
    SIREN_ALARM_DURATION_KEY,
    SIREN_VOLUME_LEVEL_KEY,
    DeviceState,
)
from custom_components.aegis_ajax.device_handlers import capabilities_for
from custom_components.aegis_ajax.number import (
    AjaxSirenAlarmDurationNumber,
)
from custom_components.aegis_ajax.number import (
    async_setup_entry as number_setup_entry,
)
from custom_components.aegis_ajax.select import (
    AjaxSirenVolumeSelect,
)
from custom_components.aegis_ajax.select import (
    async_setup_entry as select_setup_entry,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from contextlib import AbstractContextManager

    from google.protobuf.message import Message


def _street_siren(*, alarm_duration: int | None = None, volume_level: int | None = None) -> Message:
    from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import (
        hub_device_pb2,
        street_siren_pb2,
    )
    from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device.common import (
        common_siren_part_pb2,
    )

    settings = common_siren_part_pb2.CommonSirenPart.SirenSettings()
    if alarm_duration is not None:
        settings.alarm_duration = alarm_duration
    if volume_level is not None:
        settings.siren_volume_level = volume_level
    return hub_device_pb2.HubDevice(
        street_siren=street_siren_pb2.StreetSiren(
            common_siren_part=common_siren_part_pb2.CommonSirenPart(siren_settings=settings)
        )
    )


def _make_device(
    statuses: dict,
    *,
    state: DeviceState = DeviceState.ONLINE,
    device_type: str = "street_siren",
) -> Device:
    return Device(
        id="30EA219B",
        hub_id="hub-1",
        name="Siren",
        device_type=device_type,
        room_id=None,
        group_id=None,
        state=state,
        malfunctions=0,
        bypassed=False,
        statuses=statuses,
        battery=None,
    )


_SIREN_SETTINGS_DEVICE_TYPES = tuple(
    sorted(
        device_type
        for device_type in device_handlers._DEVICE_HANDLERS
        if capabilities_for(_make_device({}, device_type=device_type)).has_siren_settings
    )
)


class TestSirenSkuCoverage:
    """#354: the SKUs whose oneof case the HubDevice proto used to be missing.

    A SKU absent from the `device` oneof decodes as an unknown case, so its
    settings were unreadable and its entities would have sat permanently empty
    — which is why they have no siren-settings capability.
    """

    NEW_SKUS = (
        "street_siren_s",
        "street_siren_double_deck",
        "street_siren_s_double_deck",
        "street_siren_fibra",
        "street_siren_double_deck_fibra",
        "street_siren_plus_fibra",
    )

    @staticmethod
    def _hub_device(oneof_name: str, *, alarm_duration: int, volume_level: int) -> Message:
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import hub_device_pb2

        hub_device = hub_device_pb2.HubDevice()
        settings = getattr(hub_device, oneof_name).common_siren_part.siren_settings
        settings.alarm_duration = alarm_duration
        settings.siren_volume_level = volume_level
        return hub_device

    @pytest.mark.parametrize("oneof_name", NEW_SKUS)
    def test_settings_survive_a_serialise_round_trip(self, oneof_name: str) -> None:
        """Decode from the wire, not just from a locally-built message.

        Building the message in-process would pass even if the oneof case were
        missing from the *compiled* descriptor, which is the thing under test.
        """
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import hub_device_pb2

        raw = self._hub_device(oneof_name, alarm_duration=90, volume_level=18).SerializeToString()
        decoded = hub_device_pb2.HubDevice()
        decoded.ParseFromString(raw)

        assert decoded.WhichOneof("device") == oneof_name
        assert parse_hub_device_siren_settings(decoded) == {
            SIREN_ALARM_DURATION_KEY: 90,
            SIREN_VOLUME_LEVEL_KEY: 18,
        }

    @pytest.mark.parametrize("device_type", NEW_SKUS)
    def test_device_type_has_siren_settings_capability(self, device_type: str) -> None:
        """The proto oneof and the entity gate have to move together."""
        assert capabilities_for(_make_device({}, device_type=device_type)).has_siren_settings

    @pytest.mark.parametrize("device_type", _SIREN_SETTINGS_DEVICE_TYPES)
    def test_every_gated_type_is_a_real_object_type(self, device_type: str) -> None:
        """A device_type that `ObjectType` doesn't know can never be produced.

        `parse_device` derives `device_type` from the `ObjectType` oneof, and
        the write path turns it back into one, so an entry here that has no
        matching ObjectType would be dead weight at best and a write error at
        worst. This is what keeps `home_siren_plus` out of the set.
        """
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels import object_type_pb2

        assert hasattr(object_type_pb2.ObjectType(), device_type)

    @pytest.mark.parametrize("device_type", _SIREN_SETTINGS_DEVICE_TYPES)
    def test_every_gated_type_can_be_decoded(self, device_type: str) -> None:
        """Guard against the inverse drift: gated here but missing from the oneof."""
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import hub_device_pb2

        assert hub_device_pb2.HubDevice.DESCRIPTOR.fields_by_name.get(device_type) is not None


class TestParseSirenSettings:
    def test_parses_both_values(self) -> None:
        result = parse_hub_device_siren_settings(_street_siren(alarm_duration=90, volume_level=18))
        assert result == {
            SIREN_ALARM_DURATION_KEY: 90,
            SIREN_VOLUME_LEVEL_KEY: 18,
        }

    def test_parses_duration_only(self) -> None:
        result = parse_hub_device_siren_settings(_street_siren(alarm_duration=30))
        assert result == {SIREN_ALARM_DURATION_KEY: 30}

    def test_parses_volume_only(self) -> None:
        result = parse_hub_device_siren_settings(_street_siren(volume_level=29))
        assert result == {SIREN_VOLUME_LEVEL_KEY: 29}

    def test_empty_when_no_siren_settings(self) -> None:
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import (
            hub_device_pb2,
            street_siren_pb2,
        )

        hub_device = hub_device_pb2.HubDevice(street_siren=street_siren_pb2.StreetSiren())
        assert parse_hub_device_siren_settings(hub_device) == {}

    def test_empty_when_no_device_oneof(self) -> None:
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import hub_device_pb2

        assert parse_hub_device_siren_settings(hub_device_pb2.HubDevice()) == {}


def _patch_stream_hub_device(stub_class: MagicMock) -> AbstractContextManager[None]:
    mock_request_pb2 = MagicMock()
    mock_grpc_module = MagicMock(StreamHubDeviceServiceStub=stub_class)
    return patch.dict(
        "sys.modules",
        {
            "v3.mobilegwsvc.service.stream_hub_device.endpoint_pb2_grpc": mock_grpc_module,
            "v3.mobilegwsvc.service.stream_hub_device.request_pb2": mock_request_pb2,
            "v3.mobilegwsvc.service.stream_hub_device": MagicMock(
                endpoint_pb2_grpc=mock_grpc_module,
                request_pb2=mock_request_pb2,
            ),
        },
    )


def _stub_yielding(msg: MagicMock) -> MagicMock:
    async def _aiter(*args: object, **kwargs: object) -> AsyncGenerator[MagicMock, None]:
        yield msg

    stub_instance = MagicMock()
    stub_instance.execute.return_value = _aiter()
    return MagicMock(return_value=stub_instance)


class TestGetHubDeviceSirenSettings:
    @pytest.mark.asyncio
    async def test_returns_settings_from_snapshot(self) -> None:
        client = MagicMock()
        client._get_channel.return_value = MagicMock()
        client._session.get_call_metadata.return_value = []
        api = DevicesApi(client)

        msg = MagicMock()
        msg.HasField.side_effect = lambda field: field == "success"
        msg.success.WhichOneof.return_value = "snapshot"
        msg.success.snapshot.hub_device = _street_siren(alarm_duration=60, volume_level=1)

        with _patch_stream_hub_device(_stub_yielding(msg)):
            result = await api.get_hub_device_siren_settings("hub-1", "30EA219B")

        assert result == {SIREN_ALARM_DURATION_KEY: 60, SIREN_VOLUME_LEVEL_KEY: 1}

    @pytest.mark.asyncio
    async def test_empty_snapshot_reports_the_oneof_case(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """#354: `{}` alone can't say which of three causes was hit.

        nimahel's DoubleDeck reads `unknown` on `1.15.1-beta.6` even though the
        oneof case is now modelled and writes reach the hub. The log has to name
        the case the server actually sent and whether it carried a siren part,
        or the next step is guesswork.
        """
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import (
            hub_device_pb2,
            street_siren_pb2,
        )

        client = MagicMock()
        client._get_channel.return_value = MagicMock()
        client._session.get_call_metadata.return_value = []
        api = DevicesApi(client)

        msg = MagicMock()
        msg.HasField.side_effect = lambda field: field == "success"
        msg.success.WhichOneof.return_value = "snapshot"
        # A modelled case whose siren part is simply absent.
        msg.success.snapshot.hub_device = hub_device_pb2.HubDevice(
            street_siren_double_deck=street_siren_pb2.StreetSirenDoubleDeck()
        )

        with (
            caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.api.devices"),
            _patch_stream_hub_device(_stub_yielding(msg)),
        ):
            result = await api.get_hub_device_siren_settings("hub-1", "30EA219B")

        assert result == {}
        assert "carried no settings" in caplog.text
        assert "case='street_siren_double_deck'" in caplog.text
        assert "common_siren_part=False" in caplog.text

    @pytest.mark.asyncio
    async def test_empty_snapshot_reports_an_unmodelled_case_as_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The pre-#354 shape: nothing decodes, so there is no case at all. This
        # is the reading that says "extend the proto", as opposed to "this SKU
        # doesn't expose settings here".
        from systems.ajax.api.ecosystem.v2.hubsvc.commonmodels.device import hub_device_pb2

        client = MagicMock()
        client._get_channel.return_value = MagicMock()
        client._session.get_call_metadata.return_value = []
        api = DevicesApi(client)

        msg = MagicMock()
        msg.HasField.side_effect = lambda field: field == "success"
        msg.success.WhichOneof.return_value = "snapshot"
        msg.success.snapshot.hub_device = hub_device_pb2.HubDevice()

        with (
            caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.api.devices"),
            _patch_stream_hub_device(_stub_yielding(msg)),
        ):
            result = await api.get_hub_device_siren_settings("hub-1", "30EA219B")

        assert result == {}
        assert "case=None" in caplog.text

    @pytest.mark.asyncio
    async def test_a_populated_snapshot_logs_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The probe must stay silent on the healthy path, or it becomes noise on
        # every one of the 900 s sweeps.
        client = MagicMock()
        client._get_channel.return_value = MagicMock()
        client._session.get_call_metadata.return_value = []
        api = DevicesApi(client)

        msg = MagicMock()
        msg.HasField.side_effect = lambda field: field == "success"
        msg.success.WhichOneof.return_value = "snapshot"
        msg.success.snapshot.hub_device = _street_siren(alarm_duration=60, volume_level=1)

        with (
            caplog.at_level("DEBUG", logger="custom_components.aegis_ajax.api.devices"),
            _patch_stream_hub_device(_stub_yielding(msg)),
        ):
            await api.get_hub_device_siren_settings("hub-1", "30EA219B")

        assert "carried no settings" not in caplog.text

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure_message(self) -> None:
        client = MagicMock()
        client._get_channel.return_value = MagicMock()
        client._session.get_call_metadata.return_value = []
        api = DevicesApi(client)

        msg = MagicMock()
        msg.HasField.side_effect = lambda field: field == "failure"

        with _patch_stream_hub_device(_stub_yielding(msg)):
            result = await api.get_hub_device_siren_settings("hub-1", "30EA219B")

        assert result == {}


class TestUpdateSirenSettingsDispatch:
    @pytest.mark.asyncio
    async def test_send_command_routes_to_update(self) -> None:
        api = DevicesApi(MagicMock())
        api._update_siren_settings = AsyncMock()  # type: ignore[method-assign]
        cmd = DeviceCommand.set_siren_settings(
            hub_id="hub-1", device_id="30EA219B", device_type="street_siren", alarm_duration=45
        )
        await api.send_command(cmd)
        api._update_siren_settings.assert_awaited_once_with(cmd)

    @pytest.mark.asyncio
    async def test_rejects_command_with_no_values(self) -> None:
        api = DevicesApi(MagicMock())
        cmd = DeviceCommand.set_siren_settings(
            hub_id="hub-1", device_id="30EA219B", device_type="street_siren"
        )
        with pytest.raises(DeviceCommandError):
            await api._update_siren_settings(cmd)


class TestAjaxSirenAlarmDurationNumber:
    def _make(self, statuses: dict) -> tuple[AjaxSirenAlarmDurationNumber, MagicMock]:
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.devices = {"30EA219B": _make_device(statuses)}
        number = AjaxSirenAlarmDurationNumber(coordinator=coordinator, device_id="30EA219B")
        return number, coordinator

    def test_unique_id(self) -> None:
        number, _ = self._make({SIREN_ALARM_DURATION_KEY: 90})
        assert number.unique_id == "aegis_ajax_30EA219B_siren_alarm_duration"

    def test_native_value(self) -> None:
        number, _ = self._make({SIREN_ALARM_DURATION_KEY: 90})
        assert number.native_value == 90.0

    def test_native_value_none_when_absent(self) -> None:
        number, _ = self._make({})
        assert number.native_value is None

    def test_unavailable_when_offline(self) -> None:
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.devices = {
            "30EA219B": _make_device({SIREN_ALARM_DURATION_KEY: 90}, state=DeviceState.OFFLINE)
        }
        number = AjaxSirenAlarmDurationNumber(coordinator=coordinator, device_id="30EA219B")
        assert number.available is False

    @pytest.mark.asyncio
    async def test_set_value_sends_command(self) -> None:
        number, coordinator = self._make({SIREN_ALARM_DURATION_KEY: 90})
        coordinator.devices_api.send_command = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        await number.async_set_native_value(45)
        cmd = coordinator.devices_api.send_command.call_args[0][0]
        assert cmd.action == "siren_settings"
        assert cmd.alarm_duration == 45
        assert cmd.siren_volume_level is None

    @pytest.mark.asyncio
    async def test_set_value_schedules_confirm_read(self) -> None:
        number, coordinator = self._make({SIREN_ALARM_DURATION_KEY: 90})
        coordinator.devices_api.send_command = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        await number.async_set_native_value(45)
        coordinator.schedule_siren_settings_confirm.assert_called_once_with("30EA219B")


class TestAjaxSirenVolumeSelect:
    def _make(self, statuses: dict) -> tuple[AjaxSirenVolumeSelect, MagicMock]:
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.devices = {"30EA219B": _make_device(statuses)}
        select = AjaxSirenVolumeSelect(coordinator=coordinator, device_id="30EA219B")
        return select, coordinator

    def test_unique_id(self) -> None:
        select, _ = self._make({SIREN_VOLUME_LEVEL_KEY: 18})
        assert select.unique_id == "aegis_ajax_30EA219B_siren_volume_level"

    def test_options(self) -> None:
        select, _ = self._make({SIREN_VOLUME_LEVEL_KEY: 18})
        assert select.options == ["very_loud", "loud", "quiet", "disabled"]

    def test_current_option(self) -> None:
        select, _ = self._make({SIREN_VOLUME_LEVEL_KEY: 29})
        assert select.current_option == "quiet"

    def test_current_option_none_when_absent(self) -> None:
        select, _ = self._make({})
        assert select.current_option is None

    @pytest.mark.asyncio
    async def test_select_option_sends_command(self) -> None:
        select, coordinator = self._make({SIREN_VOLUME_LEVEL_KEY: 18})
        coordinator.devices_api.send_command = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        await select.async_select_option("disabled")
        cmd = coordinator.devices_api.send_command.call_args[0][0]
        assert cmd.action == "siren_settings"
        assert cmd.siren_volume_level == 32
        assert cmd.alarm_duration is None

    @pytest.mark.asyncio
    async def test_select_option_schedules_confirm_read(self) -> None:
        select, coordinator = self._make({SIREN_VOLUME_LEVEL_KEY: 18})
        coordinator.devices_api.send_command = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        await select.async_select_option("disabled")
        coordinator.schedule_siren_settings_confirm.assert_called_once_with("30EA219B")


class TestSetupEntries:
    @pytest.mark.asyncio
    async def test_number_created_for_siren_types_only(self) -> None:
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.devices = {
            # No status key yet (first boot, before the timer merges it) — the
            # entity must still be created so it doesn't need a reload (#310).
            "30EA219B": _make_device({}, device_type="street_siren"),
            "not-a-siren": _make_device({}, device_type="door_protect"),
        }
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await number_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 1
        assert added[0].unique_id == "aegis_ajax_30EA219B_siren_alarm_duration"

    @pytest.mark.asyncio
    async def test_select_created_for_siren_types_only(self) -> None:
        coordinator = MagicMock()
        coordinator.rooms = {}
        coordinator.devices = {
            "30EA219B": _make_device({}, device_type="home_siren_g3"),
            "not-a-siren": _make_device({}, device_type="door_protect"),
        }
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await select_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 1
