"""Hub object API for detailed hub data (SIM, firmware, companies)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom_components.aegis_ajax.api.client import AjaxGrpcClient

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimCardInfo:
    """SIM card information from the hub."""

    active_sim: int  # which SIM is active (1 or 2)
    status: int  # 0=NO_INFO, 1=INACTIVE, 2=ACTIVE
    imei: str

    @property
    def status_name(self) -> str:
        return {0: "unknown", 1: "inactive", 2: "active"}.get(self.status, "unknown")

    @property
    def is_active(self) -> bool:
        return self.status == 2


# Phase of an Ajax-side firmware update. `system_firmware_update` only
# appears in `streamHubObject` when an update is queued or already in
# flight, so absence of the field means "hub is up to date".
HUB_FW_STATE_NONE = "none"  # no pending update
HUB_FW_STATE_NOT_STARTED = "not_started"  # queued, hub hasn't begun
HUB_FW_STATE_DOWNLOADING = "downloading"  # server pushing bytes to hub


@dataclass(frozen=True)
class HubFirmwareUpdateInfo:
    """Pending hub firmware update, as reported by `streamHubObject`.

    `target_version` is the version string the hub will move to once
    the update completes; `state` is one of the `HUB_FW_STATE_*`
    constants. Currently-installed version is not exposed by Ajax in
    this stream, so the HA Update entity surfaces only `latest_version`.

    The Ajax cloud schedules and triggers firmware updates on its own —
    this integration never calls the install RPC, so the entity is
    informational only.
    """

    target_version: str
    state: str


class HubObjectApi:
    """API for hub-level data via streamHubObject."""

    def __init__(self, client: AjaxGrpcClient) -> None:
        self._client = client

    async def get_sim_info(self, hub_id: str) -> SimCardInfo | None:
        """Get SIM card info from streamHubObject."""
        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()

        # Build raw request: field 1 = hex_id (string)
        tag = (1 << 3) | 2
        encoded = hub_id.encode("utf-8")
        request_bytes = bytes([tag, len(encoded)]) + encoded

        method = channel.unary_stream(
            "/systems.ajax.api.mobile.v2.hubobject.HubObjectService/streamHubObject",
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )

        try:
            stream = method(request_bytes, metadata=metadata, timeout=15)
            async for raw_msg in stream:
                # Parse the first message (snapshot)
                sim_info = self._parse_sim_from_hub_object(raw_msg)
                if sim_info:
                    return sim_info
                break  # Only need the first message
        except Exception:
            _LOGGER.debug("Failed to get hub object data for %s", hub_id)

        return None

    async def get_firmware_info(self, hub_id: str) -> HubFirmwareUpdateInfo | None:
        """Get pending hub firmware update from streamHubObject (field 201).

        Returns `None` when the hub reports no pending update, when the
        stream errors, or when the payload doesn't include the field.
        The Ajax cloud only populates `system_firmware_update` when an
        update is queued or in flight, so a `None` return means "hub
        is up to date" from the cloud's perspective.
        """
        channel = self._client._get_channel()
        metadata = self._client._session.get_call_metadata()

        tag = (1 << 3) | 2
        encoded = hub_id.encode("utf-8")
        request_bytes = bytes([tag, len(encoded)]) + encoded

        method = channel.unary_stream(
            "/systems.ajax.api.mobile.v2.hubobject.HubObjectService/streamHubObject",
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )

        try:
            stream = method(request_bytes, metadata=metadata, timeout=15)
            async for raw_msg in stream:
                return self._parse_firmware_from_hub_object(raw_msg)
        except Exception:
            _LOGGER.debug("Failed to get hub firmware info for %s", hub_id)

        return None

    @staticmethod
    def _parse_firmware_from_hub_object(
        raw_msg: bytes,
    ) -> HubFirmwareUpdateInfo | None:
        """Parse the system firmware update sub-message from a StreamHubObject frame.

        Uses the generated proto class — cleaner than the manual byte
        walking the SIM path does, since this message hangs off field
        201 which is a multi-byte tag and `FromString` handles it
        without us re-implementing varint parsing.
        """
        from systems.ajax.api.mobile.v2.hubobject.stream_hub_object_request_pb2 import (  # noqa: PLC0415
            StreamHubObject,
        )

        try:
            response = StreamHubObject.FromString(raw_msg)
        except Exception:
            return None

        # `StreamHubObject` is a oneof — snapshot, create, update,
        # delete. Firmware metadata only ships in the snapshot
        # (first message of the stream); deltas carry just changed
        # fields and won't include a fresh firmware_version.
        if response.WhichOneof("item") != "snapshot":
            return None
        hub_object = response.snapshot

        if not hub_object.HasField("system_firmware_update"):
            return None

        sfu = hub_object.system_firmware_update
        status_name = sfu.status.WhichOneof("status")
        if status_name == "downloading":
            state = HUB_FW_STATE_DOWNLOADING
        elif status_name == "not_started":
            state = HUB_FW_STATE_NOT_STARTED
        else:
            # Future status values fall back to a known label rather than
            # raising — the entity stays informational either way.
            state = status_name or HUB_FW_STATE_NONE

        return HubFirmwareUpdateInfo(
            target_version=sfu.firmware_version or "",
            state=state,
        )

    @staticmethod
    def _parse_sim_from_hub_object(raw_msg: bytes) -> SimCardInfo | None:
        """Parse SIM card info from raw StreamHubObject bytes."""
        try:
            # Top level: StreamHubObject has oneof item
            # Field 1 (snapshot) wraps HubObject
            if not raw_msg or raw_msg[0] != 0x0A:  # field 1, wire type 2
                return None

            # Read HubObject length (varint)
            pos = 1
            hub_obj_len = raw_msg[pos]
            if hub_obj_len > 127:
                hub_obj_len = (hub_obj_len & 0x7F) | (raw_msg[pos + 1] << 7)
                pos += 2
            else:
                pos += 1

            hub_obj = raw_msg[pos : pos + hub_obj_len]

            # Find field 55 (SimCard) in HubObject
            # Field 55 = tag bytes: (55 << 3) | 2 = 442 = 0xBA 0x03
            sim_data = None
            p = 0
            while p < len(hub_obj):
                byte = hub_obj[p]
                if byte & 0x80:  # multi-byte tag
                    byte2 = hub_obj[p + 1]
                    field_num = ((byte2 & 0x7F) << 4) | ((byte >> 3) & 0x0F)
                    wire_type = byte & 0x07
                    p += 2
                else:
                    field_num = byte >> 3
                    wire_type = byte & 0x07
                    p += 1

                if wire_type == 2:  # length-delimited
                    length = hub_obj[p]
                    if length > 127:
                        length = (length & 0x7F) | (hub_obj[p + 1] << 7)
                        p += 2
                    else:
                        p += 1
                    if field_num == 55:
                        sim_data = hub_obj[p : p + length]
                        break
                    p += length
                elif wire_type == 0:  # varint
                    while hub_obj[p] & 0x80:
                        p += 1
                    p += 1
                else:
                    break

            if not sim_data:
                return None

            # Parse SimCard message
            active_sim = 0
            status = 0
            imei = ""
            p = 0
            while p < len(sim_data):
                byte = sim_data[p]
                field_num = byte >> 3
                wire_type = byte & 0x07
                p += 1

                if wire_type == 0:  # varint
                    val = sim_data[p]
                    p += 1
                    if field_num == 1:
                        active_sim = val
                    elif field_num == 2:
                        status = val
                elif wire_type == 2:  # length-delimited
                    length = sim_data[p]
                    p += 1
                    if field_num == 3:
                        imei = sim_data[p : p + length].decode("utf-8", errors="ignore")
                    p += length
                else:
                    break

            return SimCardInfo(active_sim=active_sim, status=status, imei=imei)

        except Exception:
            _LOGGER.debug("Failed to parse SIM card info from hub object")
            return None
