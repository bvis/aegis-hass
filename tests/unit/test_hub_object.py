"""Tests for hub_object API (SIM card data)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from custom_components.aegis_ajax.api.hub_object import (
    HUB_FW_STATE_DOWNLOADING,
    HUB_FW_STATE_NOT_STARTED,
    HubFirmwareUpdateInfo,
    HubObjectApi,
    SimCardInfo,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


# ---------------------------------------------------------------------------
# Helpers for building raw protobuf bytes
# ---------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    result = []
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _encode_field_varint(field_num: int, value: int) -> bytes:
    """Encode a varint field (wire type 0)."""
    tag = (field_num << 3) | 0
    return _encode_varint(tag) + _encode_varint(value)


def _encode_field_bytes(field_num: int, data: bytes) -> bytes:
    """Encode a length-delimited field (wire type 2)."""
    tag = (field_num << 3) | 2
    return _encode_varint(tag) + _encode_varint(len(data)) + data


def _build_sim_card_bytes(active_sim: int, status: int, imei: str) -> bytes:
    """Build raw bytes for a SimCard protobuf message."""
    sim = b""
    sim += _encode_field_varint(1, active_sim)
    sim += _encode_field_varint(2, status)
    sim += _encode_field_bytes(3, imei.encode("utf-8"))
    return sim


def _build_hub_object_bytes(sim_bytes: bytes) -> bytes:
    """Embed SimCard bytes as field 55 inside a HubObject message."""
    # Field 55 tag = (55 << 3) | 2 = 442
    tag = _encode_varint(442)
    length = _encode_varint(len(sim_bytes))
    return tag + length + sim_bytes


def _build_stream_hub_object_bytes(hub_obj_bytes: bytes) -> bytes:
    """Wrap HubObject bytes as field 1 (snapshot) of StreamHubObject."""
    return _encode_field_bytes(1, hub_obj_bytes)


# ---------------------------------------------------------------------------
# SimCardInfo unit tests
# ---------------------------------------------------------------------------


class TestSimCardInfo:
    def test_status_name_active(self) -> None:
        sim = SimCardInfo(active_sim=1, status=2, imei="123456789012345")
        assert sim.status_name == "active"

    def test_status_name_inactive(self) -> None:
        sim = SimCardInfo(active_sim=1, status=1, imei="")
        assert sim.status_name == "inactive"

    def test_status_name_unknown(self) -> None:
        sim = SimCardInfo(active_sim=0, status=0, imei="")
        assert sim.status_name == "unknown"

    def test_status_name_unknown_for_unrecognised_value(self) -> None:
        sim = SimCardInfo(active_sim=0, status=99, imei="")
        assert sim.status_name == "unknown"

    def test_is_active_true(self) -> None:
        sim = SimCardInfo(active_sim=1, status=2, imei="123")
        assert sim.is_active is True

    def test_is_active_false_when_inactive(self) -> None:
        sim = SimCardInfo(active_sim=1, status=1, imei="123")
        assert sim.is_active is False

    def test_is_active_false_when_no_info(self) -> None:
        sim = SimCardInfo(active_sim=0, status=0, imei="")
        assert sim.is_active is False

    def test_frozen_dataclass(self) -> None:
        sim = SimCardInfo(active_sim=1, status=2, imei="123")
        with pytest.raises(AttributeError):
            sim.status = 0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# HubObjectApi._parse_sim_from_hub_object (static parser) tests
# ---------------------------------------------------------------------------


class TestParseSimFromHubObject:
    def _make_raw_msg(self, active_sim: int, status: int, imei: str) -> bytes:
        sim_bytes = _build_sim_card_bytes(active_sim, status, imei)
        hub_obj_bytes = _build_hub_object_bytes(sim_bytes)
        return _build_stream_hub_object_bytes(hub_obj_bytes)

    def test_parse_active_sim(self) -> None:
        raw = self._make_raw_msg(active_sim=1, status=2, imei="352999001234567")
        result = HubObjectApi._parse_sim_from_hub_object(raw)
        assert result is not None
        assert result.active_sim == 1
        assert result.status == 2
        assert result.imei == "352999001234567"

    def test_parse_inactive_sim(self) -> None:
        raw = self._make_raw_msg(active_sim=2, status=1, imei="111111111111111")
        result = HubObjectApi._parse_sim_from_hub_object(raw)
        assert result is not None
        assert result.status == 1
        assert result.active_sim == 2

    def test_parse_no_info_status(self) -> None:
        raw = self._make_raw_msg(active_sim=0, status=0, imei="")
        result = HubObjectApi._parse_sim_from_hub_object(raw)
        assert result is not None
        assert result.status == 0
        assert result.imei == ""

    def test_returns_none_for_empty_bytes(self) -> None:
        assert HubObjectApi._parse_sim_from_hub_object(b"") is None

    def test_returns_none_when_first_byte_not_field1(self) -> None:
        # Field 2 wire type 2 instead of field 1
        raw = _encode_field_bytes(2, b"\x00")
        assert HubObjectApi._parse_sim_from_hub_object(raw) is None

    def test_returns_none_when_no_sim_field_in_hub_object(self) -> None:
        # Build a HubObject with only field 1 (no field 55)
        hub_obj_bytes = _encode_field_varint(1, 42)
        raw = _encode_field_bytes(1, hub_obj_bytes)
        assert HubObjectApi._parse_sim_from_hub_object(raw) is None

    def test_returns_none_for_garbage_bytes(self) -> None:
        assert HubObjectApi._parse_sim_from_hub_object(b"\xff\xff\xff\xff") is None


# ---------------------------------------------------------------------------
# HubObjectApi.get_sim_info integration tests
# ---------------------------------------------------------------------------


class TestGetSimInfo:
    def _make_api(self) -> HubObjectApi:
        client = MagicMock()
        return HubObjectApi(client)

    def _make_raw_msg(self, active_sim: int = 1, status: int = 2, imei: str = "123") -> bytes:
        sim_bytes = _build_sim_card_bytes(active_sim, status, imei)
        hub_obj_bytes = _build_hub_object_bytes(sim_bytes)
        return _build_stream_hub_object_bytes(hub_obj_bytes)

    @pytest.mark.asyncio
    async def test_returns_sim_info_from_first_message(self) -> None:
        api = self._make_api()
        raw = self._make_raw_msg(active_sim=1, status=2, imei="352999001234567")

        async def _fake_stream() -> AsyncGenerator[bytes, None]:
            yield raw

        mock_method = MagicMock(return_value=_fake_stream())
        api._client._get_channel().unary_stream.return_value = mock_method
        api._client._session.get_call_metadata.return_value = []

        result = await api.get_sim_info("hub-abc123")
        assert result is not None
        assert result.imei == "352999001234567"
        assert result.status == 2

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self) -> None:
        api = self._make_api()

        async def _raising_stream() -> AsyncGenerator[bytes, None]:
            raise RuntimeError("stream error")
            yield  # make it an async generator

        mock_method = MagicMock(return_value=_raising_stream())
        api._client._get_channel().unary_stream.return_value = mock_method
        api._client._session.get_call_metadata.return_value = []

        result = await api.get_sim_info("hub-xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_stream_empty(self) -> None:
        api = self._make_api()

        async def _empty_stream() -> AsyncGenerator[bytes, None]:
            return
            yield  # make it an async generator

        mock_method = MagicMock(return_value=_empty_stream())
        api._client._get_channel().unary_stream.return_value = mock_method
        api._client._session.get_call_metadata.return_value = []

        result = await api.get_sim_info("hub-abc")
        assert result is None


# ---------------------------------------------------------------------------
# Firmware update parser (read-only platform — never installs)
# ---------------------------------------------------------------------------


def _build_firmware_snapshot(version: str = "2.17.0", state: str = "not_started") -> bytes:
    """Build a StreamHubObject snapshot frame carrying a SystemFirmwareUpdate."""
    from google.protobuf.empty_pb2 import Empty  # noqa: PLC0415
    from systems.ajax.api.mobile.v2.hubobject.model.firmware.system_firmware_update_pb2 import (  # noqa: PLC0415
        SystemFirmwareUpdate,
    )
    from systems.ajax.api.mobile.v2.hubobject.model.hub_object_pb2 import (  # noqa: PLC0415
        HubObject,
    )
    from systems.ajax.api.mobile.v2.hubobject.stream_hub_object_request_pb2 import (  # noqa: PLC0415
        StreamHubObject,
    )

    status_kwargs: dict[str, Empty] = {state: Empty()}
    sfu = SystemFirmwareUpdate(
        firmware_version=version,
        status=SystemFirmwareUpdate.Status(**status_kwargs),
    )
    hub_obj = HubObject(hex_id="ABCD1234", system_firmware_update=sfu)
    return StreamHubObject(snapshot=hub_obj).SerializeToString()


class TestHubFirmwareUpdateInfo:
    def test_state_constants(self) -> None:
        assert HUB_FW_STATE_NOT_STARTED == "not_started"
        assert HUB_FW_STATE_DOWNLOADING == "downloading"

    def test_dataclass_is_frozen(self) -> None:
        import dataclasses  # noqa: PLC0415

        info = HubFirmwareUpdateInfo(target_version="2.17.0", state="not_started")
        with pytest.raises(dataclasses.FrozenInstanceError):
            info.target_version = "X"  # type: ignore[misc]


class TestParseFirmwareFromHubObject:
    def test_not_started_snapshot(self) -> None:
        raw = _build_firmware_snapshot(version="2.17.0", state="not_started")
        info = HubObjectApi._parse_firmware_from_hub_object(raw)
        assert info == HubFirmwareUpdateInfo(
            target_version="2.17.0", state=HUB_FW_STATE_NOT_STARTED
        )

    def test_downloading_snapshot(self) -> None:
        raw = _build_firmware_snapshot(version="2.17.0", state="downloading")
        info = HubObjectApi._parse_firmware_from_hub_object(raw)
        assert info == HubFirmwareUpdateInfo(
            target_version="2.17.0", state=HUB_FW_STATE_DOWNLOADING
        )

    def test_snapshot_without_firmware_field_returns_none(self) -> None:
        from systems.ajax.api.mobile.v2.hubobject.model.hub_object_pb2 import (  # noqa: PLC0415
            HubObject,
        )
        from systems.ajax.api.mobile.v2.hubobject.stream_hub_object_request_pb2 import (  # noqa: PLC0415
            StreamHubObject,
        )

        raw = StreamHubObject(snapshot=HubObject(hex_id="ABCD")).SerializeToString()
        assert HubObjectApi._parse_firmware_from_hub_object(raw) is None

    def test_delta_frame_returns_none(self) -> None:
        """Firmware metadata only ships in snapshot — deltas are ignored."""
        from google.protobuf.empty_pb2 import Empty  # noqa: PLC0415
        from systems.ajax.api.mobile.v2.hubobject.model.firmware.system_firmware_update_pb2 import (  # noqa: PLC0415
            SystemFirmwareUpdate,
        )
        from systems.ajax.api.mobile.v2.hubobject.model.hub_object_pb2 import (  # noqa: PLC0415
            HubObject,
        )
        from systems.ajax.api.mobile.v2.hubobject.stream_hub_object_request_pb2 import (  # noqa: PLC0415
            StreamHubObject,
        )

        sfu = SystemFirmwareUpdate(
            firmware_version="2.17.0",
            status=SystemFirmwareUpdate.Status(downloading=Empty()),
        )
        hub_obj = HubObject(hex_id="ABCD", system_firmware_update=sfu)
        raw = StreamHubObject(update=hub_obj).SerializeToString()
        assert HubObjectApi._parse_firmware_from_hub_object(raw) is None

    def test_garbage_bytes_returns_none(self) -> None:
        assert HubObjectApi._parse_firmware_from_hub_object(b"\xff\xff\xff") is None

    def test_empty_target_version_string(self) -> None:
        # Defensive: if Ajax sends an empty version string, the parser
        # still returns an info object with state but caller can choose
        # to suppress display via `target_version or None`.
        from google.protobuf.empty_pb2 import Empty  # noqa: PLC0415
        from systems.ajax.api.mobile.v2.hubobject.model.firmware.system_firmware_update_pb2 import (  # noqa: PLC0415
            SystemFirmwareUpdate,
        )
        from systems.ajax.api.mobile.v2.hubobject.model.hub_object_pb2 import (  # noqa: PLC0415
            HubObject,
        )
        from systems.ajax.api.mobile.v2.hubobject.stream_hub_object_request_pb2 import (  # noqa: PLC0415
            StreamHubObject,
        )

        sfu = SystemFirmwareUpdate(status=SystemFirmwareUpdate.Status(not_started=Empty()))
        hub_obj = HubObject(hex_id="ABCD", system_firmware_update=sfu)
        raw = StreamHubObject(snapshot=hub_obj).SerializeToString()
        info = HubObjectApi._parse_firmware_from_hub_object(raw)
        assert info is not None
        assert info.target_version == ""


class TestGetFirmwareInfo:
    def _make_api(self) -> HubObjectApi:
        client = MagicMock()
        return HubObjectApi(client)

    @pytest.mark.asyncio
    async def test_returns_firmware_info_from_snapshot(self) -> None:
        api = self._make_api()
        raw = _build_firmware_snapshot(version="2.17.0", state="downloading")

        async def _fake_stream() -> AsyncGenerator[bytes, None]:
            yield raw

        mock_method = MagicMock(return_value=_fake_stream())
        api._client._get_channel().unary_stream.return_value = mock_method
        api._client._session.get_call_metadata.return_value = []

        result = await api.get_firmware_info("ABCD1234")
        assert result == HubFirmwareUpdateInfo(
            target_version="2.17.0", state=HUB_FW_STATE_DOWNLOADING
        )

    @pytest.mark.asyncio
    async def test_returns_none_on_stream_exception(self) -> None:
        api = self._make_api()

        async def _raising_stream() -> AsyncGenerator[bytes, None]:
            raise RuntimeError("stream error")
            yield

        mock_method = MagicMock(return_value=_raising_stream())
        api._client._get_channel().unary_stream.return_value = mock_method
        api._client._session.get_call_metadata.return_value = []

        result = await api.get_firmware_info("ABCD")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_stream_empty(self) -> None:
        api = self._make_api()

        async def _empty_stream() -> AsyncGenerator[bytes, None]:
            return
            yield

        mock_method = MagicMock(return_value=_empty_stream())
        api._client._get_channel().unary_stream.return_value = mock_method
        api._client._session.get_call_metadata.return_value = []

        result = await api.get_firmware_info("ABCD")
        assert result is None
